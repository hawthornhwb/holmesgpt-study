"""Serve local trace journals or export an offline HTML snapshot."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from study.trace_viewer import DEFAULT_RUNS, export_run, load_trace, render_html


def available_runs(root: Path) -> dict[str, Path]:
    root = root.resolve()
    paths = [root / "events.jsonl"] if (root / "events.jsonl").is_file() else root.glob("*/events.jsonl")
    return {path.parent.name: path for path in sorted(paths, reverse=True)
            if path.is_file() and path.resolve().is_relative_to(root)}


def make_handler(root: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            # Only loopback Host values are accepted, including under DNS rebinding.
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.send_error(403)
                return
            route = urlsplit(self.path)
            try:
                if route.path == "/":
                    body = render_html().encode("utf-8")
                    content_type = "text/html; charset=utf-8"
                elif route.path == "/api/runs":
                    runs = []
                    for name, path in available_runs(root).items():
                        try:
                            runs.append(load_trace(path)["summary"])
                        except (OSError, ValueError) as error:
                            runs.append({"name": name, "status": "unreadable", "error": str(error)})
                    body = json.dumps(runs, ensure_ascii=False).encode("utf-8")
                    content_type = "application/json; charset=utf-8"
                elif route.path in {"/api/trace", "/export"}:
                    name = parse_qs(route.query).get("run", [""])[0]
                    path = available_runs(root).get(name)
                    if path is None:
                        self.send_error(404)
                        return
                    trace = load_trace(path)
                    if route.path == "/export":
                        body = render_html(trace).encode("utf-8")
                        content_type = "text/html; charset=utf-8"
                    else:
                        body = json.dumps(trace, ensure_ascii=False).encode("utf-8")
                        content_type = "application/json; charset=utf-8"
                else:
                    self.send_error(404)
                    return
            except (OSError, ValueError) as error:
                self.send_error(400, explain=str(error))
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            if route.path == "/export":
                self.send_header("Content-Disposition", 'attachment; filename="holmes-trace.html"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="在本机浏览器查看运行记录")
    serve.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    serve.add_argument("--port", type=int, default=8765)
    export = commands.add_parser("export", help="生成可离线打开的 HTML")
    export.add_argument("run_dir", type=Path)
    export.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        try:
            print(export_run(args.run_dir, args.output))
        except (OSError, ValueError) as error:
            parser.exit(1, f"导出失败：{error}\n")
        return
    if not args.runs_dir.is_dir():
        parser.error(f"记录目录不存在：{args.runs_dir}")
    try:
        with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.runs_dir)) as server:
            print(f"Trace 查看器：http://127.0.0.1:{server.server_port}（Ctrl+C 停止）", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except OSError as error:
        parser.exit(1, f"无法启动查看器：{error}；可用 --port 指定其他端口。\n")


if __name__ == "__main__":
    main()
