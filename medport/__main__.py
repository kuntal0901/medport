import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn


def _in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    ap = argparse.ArgumentParser(description="MedPort: private medical portfolio")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--lan", action="store_true", help="listen on your home Wi-Fi: QR share links for a doctor, and the phone app")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--demo", action="store_true",
                    help="public demo: fictional sample reports, rule parser only, uploads off, data wiped on restart")
    ap.add_argument("--host", default=None, help="address to listen on (default 127.0.0.1; a demo on a server uses 0.0.0.0)")
    args = ap.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    if not args.demo and _in_use(args.port):
        # MedPort is probably already running (double-clicked twice): just show it
        print(f"MedPort is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    if args.demo:
        import tempfile
        os.environ["MEDPORT_DATA"] = tempfile.mkdtemp(prefix="medport-demo-")
    from . import main as app_module
    app_module.PORT = args.port
    app_module.HTTPS_PORT = args.port + 1
    app_module.app.state.lan = args.lan and not args.demo
    if args.demo:
        from . import demo
        demo.enable(app_module)
    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    print(f"MedPort running at {url}  (data: {app_module.store.DATA_DIR})")
    if args.lan and not args.demo:
        from . import tls
        ip = app_module._lan_ip()
        try:
            cert, key = tls.ensure_server_cert(app_module.store.DATA_DIR / "tls", ip)
        except ValueError as e:
            print(f"Phone app not started: {e}")
        else:
            https = uvicorn.Server(uvicorn.Config(app_module.app, host=host, port=app_module.HTTPS_PORT, log_level="warning",
                                                  ssl_certfile=str(cert), ssl_keyfile=str(key)))
            threading.Thread(target=https.run, daemon=True).start()
            print(f"Phone setup: http://{ip}:{args.port}/phone   (phone app: https://{ip}:{app_module.HTTPS_PORT}/m/)")
    if not args.no_browser and not args.demo:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    print("Keep this window open while you use MedPort. Close it (or press Ctrl+C) to stop.")
    uvicorn.run(app_module.app, host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
