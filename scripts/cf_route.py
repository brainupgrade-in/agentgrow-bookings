"""Add one ingress rule to the remotely managed nuc-k3s tunnel + proxied CNAME. Backs up config first."""
import json, os, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError
CF_API="https://api.cloudflare.com/client/v4"
TOKEN=os.environ["CLOUDFLARE_API_TOKEN"]; ACCT=os.environ["CLOUDFLARE_ACCOUNT_ID"]; TUNNEL=os.environ["CLOUDFLARE_TUNNEL_ID"]
ZONE=sys.argv[1]; HOSTNAME=sys.argv[2]; SERVICE=sys.argv[3]; BACKUP=sys.argv[4]
H={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"}
def api(m,p,d=None):
    try: return json.loads(urlopen(Request(CF_API+p,data=json.dumps(d).encode() if d else None,headers=H,method=m)).read())
    except HTTPError as e: print("CF API error",e.code,e.read().decode()[:300]); raise
cfg=api("GET",f"/accounts/{ACCT}/cfd_tunnel/{TUNNEL}/configurations")
config=cfg["result"]["config"]; json.dump(config,open(BACKUP,"w"),indent=1)
before=config.get("ingress",[])
ingress=[r for r in before if r.get("hostname")!=HOSTNAME]
catch=[r for r in ingress if "hostname" not in r]; rules=[r for r in ingress if "hostname" in r]
config["ingress"]=rules+[{"hostname":HOSTNAME,"service":SERVICE}]+(catch or [{"service":"http_status:404"}])
assert len(config["ingress"])==len([r for r in before if r.get("hostname")!=HOSTNAME])+1+(0 if catch else 1)
api("PUT",f"/accounts/{ACCT}/cfd_tunnel/{TUNNEL}/configurations",{"config":config})
print(f"ingress rules: {len(before)} -> {len(config['ingress'])}; added {HOSTNAME} -> {SERVICE}")
recs=api("GET",f"/zones/{ZONE}/dns_records?name={HOSTNAME}")["result"]
if recs: print("DNS exists:",[(r['type'],r['content']) for r in recs])
else:
    api("POST",f"/zones/{ZONE}/dns_records",{"type":"CNAME","name":HOSTNAME,"content":f"{TUNNEL}.cfargotunnel.com","proxied":True,"ttl":1})
    print("DNS CNAME created ->",f"{TUNNEL}.cfargotunnel.com")
