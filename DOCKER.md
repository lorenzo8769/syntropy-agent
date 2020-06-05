# Docker

    docker run --network="host" --restart=on-failure:10 --cap-add=NET_ADMIN --cap-add=SYS_MODULE  \ 
    --network="host" -v /var/run/docker.sock:/var/run/docker.sock  --name=noia-agent \
    -e NOIA_API_KEY='z99CuiZnMhe2qtz4LLX43Gbho5Zu9G8oAoWRY68WdMTVB9GzuMY2HNn667A752EA' \ 
    -e NOIA_ROLE='gateway' \
    -e NOIA_NETWORK_IDS='Lpy3zq2ehdVZehZvoRFur4tV,U7FrPST7bV6NQGyBdhHyiebg'
    -e NOIA_CITY='Frankfurt' \
    -d noia/agent


### List of networks to join. `network_ids = 0 `, then node will not join any network: 
`-e NETWORK_IDS='Lpy3zq2ehdVZehZvoRFur4tV,U7FrPST7bV6NQGyBdhHyiebg'`
### Metadata (Optional)

```ini
-e NOIA_NAME='Azure EU gateway '
-e NOIA_COUNTRY='Germany'
-e NOIA_CITY='Frankfurt'

#Select one of the categories from the list or default will be assigned 
# 'zIoT','Server','none' 
-e NOIA_CATEGORY='IoT'

#Select one of providers from the list or default will be assigned 
#'AWS', 'DigtialOcean', 'Microsoft Azure', 'Rackspace', 'Alibaba Cloud', 
#'Google Cloud Platform', 'Oracle Cloud', 'VMware', 'IBM Cloud', 'Vultr'. 

-e NOIA_PROVIDER ='Microsoft Azure 
-e NOIA_LAT='40.14'
-e NOIA_LON='-74.21'
```

#### Tags (Optional)

categorize your end-points. #You can use more than one tag.  e.g. eu-group,fr-group


```ini
-e NOIA_TAGS='Tag1,Tag2'
```