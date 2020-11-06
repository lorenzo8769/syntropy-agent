import socket
import base64
import logging
import subprocess
import re
from pathlib import Path
from pyroute2 import IPDB, WireGuard, NDB, NetlinkError
from nacl.public import PrivateKey

from platform_agent.cmd.lsmod import module_loaded
from platform_agent.cmd.wg_show import get_wg_listen_port
from platform_agent.files.tmp_files import get_peer_metadata
from platform_agent.routes import Routes
from platform_agent.wireguard.helpers import find_free_port, get_peer_info, WG_NAME_PATTERN

logger = logging.getLogger()

class WgConfException(Exception):
    pass


def delete_interface(ifname):

    subprocess.run(['ip', 'link', 'del', ifname], check=False)


class WgConf():

    def __init__(self):

        self.wg_kernel = module_loaded('wireguard')
        self.wg = WireGuard() if self.wg_kernel else WireguardGo()
        self.ipdb = IPDB()
        self.ndb = NDB()
        self.routes = Routes()

    @staticmethod
    def get_wg_interfaces():
        with IPDB() as ipdb:
            current_interfaces = [k for k, v in ipdb.by_name.items() if re.match(WG_NAME_PATTERN, k)]
        return current_interfaces

    def clear_interfaces(self, dump):
        remote_interfaces = [d['args']['ifname'] for d in dump if d['fn'] == 'create_interface']
        current_interfaces = self.get_wg_interfaces()
        remove_interfaces = set(current_interfaces) - set(remote_interfaces)
        logger.info(
            f"Clearing interfaces REMOTE - {remote_interfaces}, CURRENT - {current_interfaces} REMOVE={remove_interfaces}"
        )
        for interface in remove_interfaces:
            self.remove_interface(interface)

    def clear_peers(self, dump):
        remote_peers = [d['args']['public_key'] for d in dump if d['fn'] == 'add_peer']
        current_interfaces = self.get_wg_interfaces()
        for iface in current_interfaces:
            peers = get_peer_info(iface, self.wg)
            for peer in peers:
                if peer not in remote_peers:
                    self.remove_peer(iface, peer)

    def get_wg_keys(self, ifname):
        private_key_path = f"/etc/noia-agent/privatekey-{ifname}"
        public_key_path = f"/etc/noia-agent/publickey-{ifname}"
        private_key = Path(private_key_path)
        public_key = Path(public_key_path)
        if not private_key.is_file() or not public_key.is_file():
            privKey = PrivateKey.generate()
            pubKey = base64.b64encode(bytes(privKey.public_key))
            privKey = base64.b64encode(bytes(privKey))
            base64_privKey = privKey.decode('ascii')
            base64_pubKey = pubKey.decode('ascii')
            private_key.write_text(base64_privKey)
            public_key.write_text(base64_pubKey)
            private_key.chmod(0o600)
            public_key.chmod(0o600)

        if self.wg_kernel:
            return public_key.read_text().strip(), private_key.read_text().strip()
        else:
            return public_key.read_text().strip(), private_key_path

    def next_free_port(self, port=1024, max_port=65535):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        while port <= max_port:
            try:
                sock.bind(('', port))
                sock.close()
                return port
            except OSError:
                port += 1
        raise IOError('no free ports')

    def create_interface(self, ifname, internal_ip, listen_port=None, **kwargs):
        public_key, private_key = self.get_wg_keys(ifname)
        peer_metadata = {'metadata': get_peer_metadata(public_key)}
        logger.info(
            f"[WG_CONF] - Creating interface {ifname}, {listen_port}, {internal_ip}- wg_kernel={self.wg_kernel}",
            extra={'metadata': peer_metadata}
        )

        if self.wg_kernel:
            try:
                wg_int = self.ndb.interfaces.create(kind='wireguard', ifname=ifname)
            except KeyError as e:
                if not len(e.args) and e.args[0] == 'object exists':
                    raise WgConfException(str(e))
        else:
            self.wg.create_interface(ifname)
            from time import sleep
            #Wait until interface was created
            sleep(0.01)

        if self.ndb.interfaces.get(ifname):
            wg_int = self.ndb.interfaces[ifname]
        elif not wg_int:
            raise WgConfException("Wireguard failed to create interface")
        wg_int.add_ip(internal_ip)
        wg_int.set('state', 'up')
        try:
            wg_int.commit()
        except KeyError as e:
            if not len(e.args) and e.args[0] == 'object exists':
                raise WgConfException(str(e))
        try:
            self.wg.set(
                ifname,
                private_key=private_key,
                listen_port=listen_port
            )
        except NetlinkError as error:
            if error.code != 98:
                raise
            else:
                # if port was taken before creating.
                self.wg.set(
                    ifname,
                    private_key=private_key,
                )
        listen_port = self.get_listening_port(ifname)
        if not listen_port:
            listen_port = find_free_port()
            self.wg.set(
                ifname,
                private_key=private_key,
                listen_port=listen_port
            )

        result = {
            "public_key": public_key,
            "listen_port": int(listen_port),
            "ifname": ifname
        }
        logger.info(
            f"[WG_CONF] - interface_created {result}",
            extra={'metadata': peer_metadata}
        )
        return result

    def add_peer(self, ifname, public_key, allowed_ips, gw_ipv4, endpoint_ipv4=None, endpoint_port=None):
        if self.wg_kernel:
            try:
                peer_info = get_peer_info(ifname=ifname, wg=self.wg)
            except ValueError as e:
                raise WgConfException(str(e))
            old_ips = set(peer_info.get(public_key, [])) - set(allowed_ips)
            self.routes.ip_route_del(ifname, old_ips)
        peer = {'public_key': public_key,
                'endpoint_addr': endpoint_ipv4,
                'endpoint_port': endpoint_port,
                'persistent_keepalive': 15,
                'allowed_ips': allowed_ips}
        self.wg.set(ifname, peer=peer)
        self.routes.ip_route_add(ifname, allowed_ips, gw_ipv4)
        return

    def remove_peer(self, ifname, public_key, allowed_ips=None):

        if not self.ndb.interfaces.get(ifname):
            logger.warning(f'[WG_CONF] Remove peer - [{ifname}] does not exist')
            return

        peer = {
            'public_key': public_key,
            'remove': True
        }

        self.wg.set(ifname, peer=peer)
        if allowed_ips:
            self.routes.ip_route_del(ifname, allowed_ips)
        return

    def remove_interface(self, ifname):
        delete_interface(ifname)

    def get_listening_port(self, ifname):
        if self.wg_kernel:
            wg_info = dict(self.wg.info(ifname)[0]['attrs'])
            return wg_info['WGDEVICE_A_LISTEN_PORT']

        else:
            wg_info = self.wg.info(ifname)
            return wg_info['listen_port']


class WireguardGo:

    def set(self, ifname, peer=None, private_key=None, listen_port=None):
        full_cmd = f"wg set {ifname}".split(' ')
        if peer:
            allowed_ips_cmd = ""
            if not peer.get('remove'):
                for ip in peer.get('allowed_ips', []):
                    allowed_ips_cmd += f"allowed-ips {ip} "
                peer_cmd = f"peer {peer['public_key']} {allowed_ips_cmd}endpoint {peer['endpoint_addr']}:{peer['endpoint_port']}".split(' ')
            else:
                peer_cmd = f"peer {peer['public_key']} remove"
            full_cmd += peer_cmd
        if private_key:
            private_key_cmd = f"private-key {private_key}".split(' ')
            full_cmd += private_key_cmd
            if not listen_port:
                listen_port = find_free_port()
        if listen_port:
            listen_port_cmd = f"listen-port {listen_port}".split(' ')
            full_cmd += listen_port_cmd

        result_set = subprocess.run(full_cmd, encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        complete_output = result_set.stdout or result_set.stderr
        complete_output = complete_output or 'Success'
        logger.info(f"[Wireguard-go] - WG SET - {complete_output} , args {full_cmd}")
        return complete_output

    def create_interface(self, ifname):
        try:
            result_set = subprocess.Popen(
                ['wireguard-go', ifname],
                encoding='utf-8',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )
        except FileNotFoundError:
            raise WgConfException(f'Wireguard-go missing')

        complete_output = result_set.stdout or result_set.stderr
        complete_output = complete_output or 'Success'
        logger.info(f"[Wireguard-go] - WG Create - {complete_output} , args {ifname}")
        return complete_output

    def info(self, ifname):
        return {
            "listen_port": get_wg_listen_port(ifname)
        }
