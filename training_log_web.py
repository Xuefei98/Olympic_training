#!/usr/bin/env python3
"""
Local web interface for training videos and coach comments.

Run:
  python3 training_log_web.py
Then open:
  http://127.0.0.1:8000/webui/
"""

from __future__ import annotations

import argparse
import json
import errno
import re
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


# Section headers we recognise inside *.summary.md files.
SUMMARY_SECTION_TITLES: list[str] = [
    "关键问题",
    "改进建议",
    "下次训练检查点",
]

# Selectable movements shown in the bullet-level dropdowns. Order matters:
# the most common Olympic-lifting / strength movements come first.
MOVEMENT_OPTIONS: list[str] = [
    "抓举",
    "挺举",
    "上挺",
    "借力推",
    "高翻",
    "悬垂高翻",
    "悬垂抓举",
    "前蹲",
    "后蹲",
    "硬拉",
    "抓举硬拉",
    "挺举硬拉",
    "抓举发力",
    "挺举发力",
    "抓举支撑",
    "挺举支撑",
    "上拉",
    "下拉",
    "推举",
    "借力挺",
    "分腿挺",
    "颈后推举",
    "箭步蹲",
    "罗马尼亚硬拉",
    "过头蹲",
    "辅助训练",
    "通用",
    "未知",
]

# Markdown patterns we use to read/write bullet movement tags.
SECTION_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$")
# Bullet line: "- [动作: XXX] some text" or just "- some text".
# Group 1 = optional movement label, group 2 = remaining text.
BULLET_RE = re.compile(
    r"^-\s+(?:\[\s*动作\s*[:：]\s*([^\]]+?)\s*\]\s*)?(.*)$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve training video comment web UI.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind (default: 8000).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Training folder root containing MP4 files (default: current dir).",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("training_log_output"),
        help="Directory containing feedback CSV/JSON (default: training_log_output).",
    )
    parser.add_argument(
        "--auto-stop-seconds",
        type=int,
        default=20,
        help=(
            "Stop server if no browser heartbeat is received for this many seconds "
            "(default: 20). Set to 0 to disable."
        ),
    )
    return parser.parse_args()


def list_videos(root: Path) -> list[str]:
    out: list[str] = []
    for p in sorted(root.iterdir()):
        if p.is_file() and p.suffix.lower() == ".mp4":
            out.append(p.name)
    return out


def _safe_summary_path(root: Path, log_dir: Path, video_name: str) -> Path | None:
    """Resolve the summary path safely under the root directory."""
    stem = Path(video_name).stem
    if not stem or "/" in stem or "\\" in stem:
        return None
    path = (root / log_dir / f"{stem}.summary.md").resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def read_text_artifact(root: Path, log_dir: Path, video_name: str, suffix: str) -> str:
    stem = Path(video_name).stem
    path = (root / log_dir / f"{stem}.{suffix}").resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return ""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def has_summary_for_video(root: Path, log_dir: Path, video_name: str) -> bool:
    path = _safe_summary_path(root, log_dir, video_name)
    if path is None or not path.exists():
        return False
    return bool(path.read_text(encoding="utf-8").strip())


def parse_summary_markdown(text: str) -> dict:
    """Parse a *.summary.md file into structured sections + bullets.

    Returns:
        {
          "sections": [
            {"title": "关键问题",  "bullets": [{"id": 0, "movement": "抓举", "text": "..."}, ...]},
            {"title": "改进建议",  "bullets": [...]},
            {"title": "下次训练检查点", "bullets": [...]},
          ]
        }

    All three canonical sections are always present (with empty bullet
    lists if missing in the source) so the UI can render consistent panels.
    """
    sections_by_title: dict[str, dict] = {}
    current_title: str | None = None
    bullet_index_by_section: dict[str, int] = {}

    for line in text.splitlines():
        m_header = SECTION_HEADER_RE.match(line)
        if m_header:
            title = m_header.group(1).strip()
            if title in SUMMARY_SECTION_TITLES:
                current_title = title
                if title not in sections_by_title:
                    sections_by_title[title] = {"title": title, "bullets": []}
                    bullet_index_by_section[title] = 0
            else:
                current_title = None
            continue

        if current_title is None:
            continue

        stripped = line.strip()
        if not stripped or not stripped.startswith("-"):
            continue
        m_bullet = BULLET_RE.match(stripped)
        if not m_bullet:
            continue
        movement = (m_bullet.group(1) or "").strip()
        body = (m_bullet.group(2) or "").strip()
        sections_by_title[current_title]["bullets"].append(
            {
                "id": bullet_index_by_section[current_title],
                "movement": movement,
                "text": body,
            }
        )
        bullet_index_by_section[current_title] += 1

    sections = []
    for title in SUMMARY_SECTION_TITLES:
        if title in sections_by_title:
            sections.append(sections_by_title[title])
        else:
            sections.append({"title": title, "bullets": []})
    return {"sections": sections}


def update_bullet_in_markdown(
    text: str,
    section_title: str,
    bullet_id: int,
    *,
    movement: str | None = None,
    new_text: str | None = None,
) -> str:
    """Return updated markdown with the given bullet's movement and/or body changed.

    Args:
        text: full *.summary.md contents
        section_title: must be one of SUMMARY_SECTION_TITLES
        bullet_id: 0-based index within that section
        movement: None = leave existing tag untouched; "" / "未指定" = clear tag;
                  non-empty string = set tag to that value.
        new_text: None = leave body untouched; non-empty string = replace body.

    Raises:
        ValueError if the section/bullet cannot be located, both fields are
        None, or `new_text` is empty after stripping.
    """
    if section_title not in SUMMARY_SECTION_TITLES:
        raise ValueError(f"Unsupported section: {section_title}")
    if movement is None and new_text is None:
        raise ValueError("Must specify at least one of: movement, new_text")

    lines = text.splitlines()
    in_target_section = False
    seen_bullets = 0
    target_line_idx: int | None = None

    for idx, line in enumerate(lines):
        m_header = SECTION_HEADER_RE.match(line)
        if m_header:
            title = m_header.group(1).strip()
            if title == section_title:
                in_target_section = True
                seen_bullets = 0
            else:
                in_target_section = False
            continue
        if not in_target_section:
            continue
        stripped = line.strip()
        if not stripped or not stripped.startswith("-"):
            continue
        if not BULLET_RE.match(stripped):
            continue
        if seen_bullets == bullet_id:
            target_line_idx = idx
            break
        seen_bullets += 1

    if target_line_idx is None:
        raise ValueError(
            f"Bullet not found: section={section_title!r} id={bullet_id}"
        )

    original = lines[target_line_idx]
    leading_ws_len = len(original) - len(original.lstrip())
    leading_ws = original[:leading_ws_len]
    stripped = original[leading_ws_len:]
    m = BULLET_RE.match(stripped)
    if not m:
        raise ValueError(f"Could not re-match bullet on line: {original!r}")
    current_movement = (m.group(1) or "").strip()
    current_body = (m.group(2) or "").strip()

    # Resolve final movement.
    if movement is None:
        final_movement = current_movement
    else:
        cleaned = movement.strip()
        final_movement = "" if cleaned in ("", "未指定") else cleaned

    # Resolve final body. We deliberately strip leading/trailing whitespace
    # from user-supplied edits but otherwise preserve internal whitespace.
    if new_text is None:
        final_body = current_body
    else:
        # Collapse internal newlines to a space so a single bullet stays
        # a single markdown line.
        final_body = " ".join(new_text.split("\n")).strip()
        if not final_body:
            raise ValueError("Bullet text cannot be empty")

    if final_movement:
        new_line = f"{leading_ws}- [动作: {final_movement}] {final_body}"
    else:
        new_line = f"{leading_ws}- {final_body}"
    lines[target_line_idx] = new_line

    new_md = "\n".join(lines)
    if text.endswith("\n") and not new_md.endswith("\n"):
        new_md += "\n"
    return new_md


def delete_bullet_in_markdown(text: str, section_title: str, bullet_id: int) -> str:
    """Return markdown with the selected bullet removed from a section."""
    if section_title not in SUMMARY_SECTION_TITLES:
        raise ValueError(f"Unsupported section: {section_title}")

    lines = text.splitlines()
    in_target_section = False
    seen_bullets = 0
    target_line_idx: int | None = None

    for idx, line in enumerate(lines):
        m_header = SECTION_HEADER_RE.match(line)
        if m_header:
            title = m_header.group(1).strip()
            if title == section_title:
                in_target_section = True
                seen_bullets = 0
            else:
                in_target_section = False
            continue
        if not in_target_section:
            continue
        stripped = line.strip()
        if not stripped or not stripped.startswith("-"):
            continue
        if not BULLET_RE.match(stripped):
            continue
        if seen_bullets == bullet_id:
            target_line_idx = idx
            break
        seen_bullets += 1

    if target_line_idx is None:
        raise ValueError(
            f"Bullet not found: section={section_title!r} id={bullet_id}"
        )

    del lines[target_line_idx]
    new_md = "\n".join(lines)
    if text.endswith("\n") and not new_md.endswith("\n"):
        new_md += "\n"
    return new_md


# Backwards-compat alias used by the legacy /api/summary/movement endpoint.
def update_bullet_movement_in_markdown(
    text: str, section_title: str, bullet_id: int, movement: str
) -> str:
    return update_bullet_in_markdown(
        text, section_title, bullet_id, movement=movement
    )


class Handler(SimpleHTTPRequestHandler):
    root_dir: Path
    log_dir: Path
    last_heartbeat_ts: float = 0.0

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> tuple[dict | None, str | None]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None, "Invalid Content-Length"
        if content_length <= 0 or content_length > 1_000_000:
            return None, "Empty or oversized body"
        raw = self.rfile.read(content_length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"Invalid JSON: {exc}"
        if not isinstance(payload, dict):
            return None, "JSON body must be an object"
        return payload, None

    def do_GET(self) -> None:  # noqa: N802 (HTTP method name)
        parsed = urlparse(self.path)
        Handler.last_heartbeat_ts = time.time()
        if parsed.path == "/api/videos":
            videos = list_videos(self.root_dir)
            items = [
                {
                    "name": video,
                    "comment_count": int(
                        has_summary_for_video(
                            self.root_dir,
                            self.log_dir,
                            video,
                        )
                    ),
                }
                for video in videos
            ]
            self._send_json({"videos": items})
            return

        if parsed.path == "/api/transcript":
            query = parse_qs(parsed.query)
            video = (query.get("video") or [""])[0]
            if not video:
                self._send_json({"error": "Missing query parameter: video"}, status=400)
                return
            text = read_text_artifact(
                self.root_dir, self.log_dir, video, "transcript.txt"
            )
            self._send_json({"video": video, "transcript": text})
            return

        if parsed.path == "/api/summary":
            query = parse_qs(parsed.query)
            video = (query.get("video") or [""])[0]
            if not video:
                self._send_json({"error": "Missing query parameter: video"}, status=400)
                return
            text = read_text_artifact(self.root_dir, self.log_dir, video, "summary.md")
            structured = parse_summary_markdown(text)
            self._send_json(
                {
                    "video": video,
                    "summary": text,
                    "sections": structured["sections"],
                }
            )
            return

        if parsed.path == "/api/movements":
            self._send_json({"movements": MOVEMENT_OPTIONS})
            return

        if parsed.path == "/api/heartbeat":
            self._send_json({"ok": True, "ts": int(time.time())})
            return

        super().do_GET()

    def _handle_bullet_update(self, *, allow_text: bool) -> None:
        """Shared logic for /api/summary/bullet (full) and /api/summary/movement (legacy)."""
        payload, err = self._read_json_body()
        if err is not None or payload is None:
            self._send_json({"error": err or "Invalid body"}, status=400)
            return

        video = str(payload.get("video", "")).strip()
        section = str(payload.get("section", "")).strip()
        try:
            bullet_id = int(payload.get("bullet_id", -1))
        except (TypeError, ValueError):
            bullet_id = -1

        # `movement` and `text` are optional; presence (not just truthiness)
        # determines whether they get applied.
        movement_raw = payload.get("movement", None)
        text_raw = payload.get("text", None) if allow_text else None

        movement_arg = None if movement_raw is None else str(movement_raw)
        text_arg = None if text_raw is None else str(text_raw)

        if movement_arg is None and text_arg is None:
            self._send_json(
                {"error": "Must specify at least one of: movement, text"},
                status=400,
            )
            return
        if not video or not section or bullet_id < 0:
            self._send_json(
                {"error": "Missing required fields (video, section, bullet_id)"},
                status=400,
            )
            return
        if section not in SUMMARY_SECTION_TITLES:
            self._send_json(
                {"error": f"Unsupported section: {section}"}, status=400
            )
            return

        path = _safe_summary_path(self.root_dir, self.log_dir, video)
        if path is None:
            self._send_json({"error": "Invalid video name"}, status=400)
            return
        if not path.exists():
            self._send_json({"error": "Summary file not found"}, status=404)
            return

        text = path.read_text(encoding="utf-8")
        try:
            new_text = update_bullet_in_markdown(
                text,
                section,
                bullet_id,
                movement=movement_arg,
                new_text=text_arg,
            )
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        path.write_text(new_text, encoding="utf-8")
        structured = parse_summary_markdown(new_text)
        self._send_json(
            {
                "ok": True,
                "video": video,
                "section": section,
                "bullet_id": bullet_id,
                "sections": structured["sections"],
            }
        )

    def do_POST(self) -> None:  # noqa: N802 (HTTP method name)
        parsed = urlparse(self.path)
        Handler.last_heartbeat_ts = time.time()

        if parsed.path == "/api/summary/bullet":
            self._handle_bullet_update(allow_text=True)
            return

        if parsed.path == "/api/summary/movement":
            # Legacy endpoint kept for older clients; movement-only.
            self._handle_bullet_update(allow_text=False)
            return

        if parsed.path == "/api/summary/bullet/delete":
            payload, err = self._read_json_body()
            if err is not None or payload is None:
                self._send_json({"error": err or "Invalid body"}, status=400)
                return

            video = str(payload.get("video", "")).strip()
            section = str(payload.get("section", "")).strip()
            try:
                bullet_id = int(payload.get("bullet_id", -1))
            except (TypeError, ValueError):
                bullet_id = -1

            if not video or not section or bullet_id < 0:
                self._send_json(
                    {"error": "Missing required fields (video, section, bullet_id)"},
                    status=400,
                )
                return
            if section not in SUMMARY_SECTION_TITLES:
                self._send_json(
                    {"error": f"Unsupported section: {section}"}, status=400
                )
                return

            path = _safe_summary_path(self.root_dir, self.log_dir, video)
            if path is None:
                self._send_json({"error": "Invalid video name"}, status=400)
                return
            if not path.exists():
                self._send_json({"error": "Summary file not found"}, status=404)
                return

            text = path.read_text(encoding="utf-8")
            try:
                new_text = delete_bullet_in_markdown(text, section, bullet_id)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return

            path.write_text(new_text, encoding="utf-8")
            structured = parse_summary_markdown(new_text)
            self._send_json(
                {
                    "ok": True,
                    "video": video,
                    "section": section,
                    "bullet_id": bullet_id,
                    "sections": structured["sections"],
                }
            )
            return

        self.send_response(404)
        self.end_headers()


def start_idle_shutdown_watcher(
    server: ThreadingHTTPServer, auto_stop_seconds: int
) -> None:
    if auto_stop_seconds <= 0:
        return

    def watcher() -> None:
        while True:
            time.sleep(2)
            idle_for = time.time() - Handler.last_heartbeat_ts
            if idle_for >= auto_stop_seconds:
                print(
                    f"No browser heartbeat for {auto_stop_seconds}s. "
                    "Stopping server and releasing port."
                )
                server.shutdown()
                break

    t = threading.Thread(target=watcher, daemon=True)
    t.start()


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    log_dir = args.log_dir

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Root directory does not exist: {root}")

    Handler.root_dir = root
    Handler.log_dir = log_dir
    Handler.last_heartbeat_ts = time.time()

    chosen_port = args.port
    server = None
    max_tries = 20
    for _ in range(max_tries):
        try:
            server = ThreadingHTTPServer((args.host, chosen_port), Handler)
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            chosen_port += 1

    if server is None:
        raise SystemExit(
            f"Could not bind to a free port in range {args.port}-{chosen_port}."
        )

    # Serve static files and videos directly from the training folder.
    import os

    os.chdir(root)
    print(f"Serving: {root}")
    print(f"Open:   http://{args.host}:{chosen_port}/webui/")
    if args.auto_stop_seconds > 0:
        print(
            f"Auto-stop: server exits after {args.auto_stop_seconds}s "
            "without web page heartbeat."
        )
    else:
        print("Auto-stop: disabled")
    print("Press Ctrl+C to stop.")
    start_idle_shutdown_watcher(server, args.auto_stop_seconds)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
