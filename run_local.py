"""Launch the local frontend and Django API as separate processes."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
ENV = os.environ.copy()
API_PORT = ENV.setdefault("LEDGER_API_PORT", "51801")
WEB_PORT = ENV.setdefault("LEDGER_WEB_PORT", "51800")


def check_ports():
    ports = {"frontend": int(WEB_PORT), "Django API": int(API_PORT)}
    if len(set(ports.values())) != len(ports):
        raise ValueError("Choose different ports for the frontend and Django API.")
    for service, port in ports.items():
        if not 1 <= port <= 65535:
            raise ValueError(f"Enter a valid port for the {service}.")
        with socket.socket() as connection:
            connection.settimeout(0.4)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError(
                    f"Port {port} is already in use for the {service}. "
                    "Stop the existing server or choose another port."
                )


def main():
    try:
        check_ports()
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    processes = []
    commands = [
        [sys.executable, "backend/manage.py", "runserver", f"127.0.0.1:{API_PORT}", "--noreload"],
        [sys.executable, "frontend/serve.py"],
    ]
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=ROOT, env=ENV))
        print(f"Open http://127.0.0.1:{WEB_PORT}/ after Django finishes starting.", flush=True)
        while True:
            for process in processes:
                if process.poll() is not None:
                    return process.returncode
            time.sleep(0.4)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            process.wait()


if __name__ == "__main__":
    sys.exit(main())