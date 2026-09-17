import base64, json, random, datetime

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

random.seed(42)
now = datetime.datetime.now(datetime.timezone.utc)

def make_cert(main, sans, days):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, main)])
    cert = (x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(
            [x509.DNSName(d) for d in [main] + sans]), critical=False)
        .sign(key, hashes.SHA256()))
    return base64.b64encode(cert.public_bytes(serialization.Encoding.PEM)).decode()

certs = [
    ("jellyfin.example.com", [], 82),
    ("wiki.example.com", [], 77),
    ("*.example.com", ["example.com"], 74),
    ("vault.example.com", [], 61),
    ("paperless.example.com", [], 52),
    ("cloud.example.com", ["office.example.com"], 47),
    ("home.example.com", [], 33),
    ("status.example.com", [], 9),
]
acme = {"letsencrypt": {"Certificates": [
    {"domain": {"main": m, "sans": s}, "certificate": make_cert(m, s, d),
     "key": "", "Store": "default"} for m, s, d in certs]}}
open("/data/acme.json", "w").write(json.dumps(acme, indent=2))

services = [
    ("jellyfin-service", "jellyfin.example.com", ["/web/index.html", "/Items/latest", "/Sessions", "/socket"], 900),
    ("immich-service", "photos.example.com", ["/api/assets", "/api/timeline", "/photos"], 700),
    ("grafana-service", "grafana.example.com", ["/api/dashboards/home", "/d/homelab/overview", "/api/datasources"], 420),
    ("nextcloud-service", "cloud.example.com", ["/remote.php/dav", "/index.php/apps/files", "/status.php"], 380),
    ("vaultwarden-service", "vault.example.com", ["/api/sync", "/identity/connect/token", "/notifications/hub"], 260),
    ("homeassistant-service", "home.example.com", ["/api/websocket", "/lovelace/0", "/api/states"], 350),
    ("sonarr-service", "sonarr.example.com", ["/api/v3/queue", "/api/v3/series", "/"], 210),
    ("uptime-kuma-service", "status.example.com", ["/dashboard", "/api/status-page/heartbeat"], 190),
    ("pihole-service", "pihole.example.com", ["/admin/api.php", "/admin/"], 150),
    ("code-server-service", "code.example.com", ["/", "/static/out/vs/workbench"], 120),
    ("authelia-service", "auth.example.com", ["/api/verify", "/api/firstfactor", "/"], 500),
    ("paperless-service", "docs.example.com", ["/api/documents/", "/dashboard/"], 90),
]
ips = ["99.228.93.14", "8.8.8.8", "1.1.1.1", "185.199.108.153", "203.0.113.7",
       "142.114.51.9", "24.212.181.77", "76.68.144.201", "184.147.62.35", "66.249.66.1",
       "51.222.87.113", "104.28.42.9"]
methods = ["GET"]*14 + ["POST"]*4 + ["PUT", "DELETE"]
lines = []
t = now - datetime.timedelta(minutes=58)
total = 320
for i in range(total):
    svc, host, paths, weight = random.choices(services, weights=[s[3] for s in services])[0], None, None, None
    svc_name, host, paths, _ = svc
    path = random.choice(paths)
    r = random.random()
    status = 200 if r < 0.86 else (304 if r < 0.90 else (301 if r < 0.92 else (404 if r < 0.955 else (429 if r < 0.97 else (502 if r < 0.985 else 500)))))
    dr = random.random()
    if dr < 0.35:   dur = random.randint(180, 950) * 1000
    elif dr < 0.85: dur = random.randint(1, 60) * 1000000 + random.randint(0, 999999)
    elif dr < 0.97: dur = random.randint(60, 900) * 1000000
    else:           dur = random.randint(1, 4) * 1000000000 + random.randint(0, 999999999)
    t += datetime.timedelta(seconds=random.uniform(2, 20))
    lines.append(json.dumps({
        "ClientHost": random.choice(ips),
        "ClientAddr": "",
        "DownstreamContentSize": random.randint(180, 512000),
        "DownstreamStatus": status,
        "Duration": dur,
        "RequestHost": host,
        "RequestMethod": random.choice(methods),
        "RequestPath": path,
        "RequestProtocol": "HTTP/2.0",
        "RouterName": svc_name.replace("-service", "") + "@file",
        "ServiceName": svc_name + "@file",
        "ServiceURL": "http://10.0.10.11:8096",
        "StartUTC": t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "entryPointName": "websecure",
        "level": "info", "msg": "",
    }))
open("/data/access.log", "w").write("\n".join(lines) + "\n")
print(f"acme.json: {len(certs)} certs, access.log: {total} lines")
