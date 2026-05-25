from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .qms_agent_service import QMSAgentService, load_config_from_env


class QMSHandler(BaseHTTPRequestHandler):
    service: QMSAgentService | None = None

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"ok": True, "service": "qms-agent-backend"})
            return

        parsed = urlparse(self.path)
        if parsed.path == "/v1/qms/memory/profile":
            if not self.service:
                raise RuntimeError("service not initialized")
            qs = parse_qs(parsed.query)
            user_id = (qs.get("user_id", ["anonymous"])[0] or "anonymous").strip()
            data = self.service.get_user_long_term_memory(user_id)
            self._send_json(HTTPStatus.OK, {"ok": True, "data": data})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8"))

            if not self.service:
                raise RuntimeError("service not initialized")

            if self.path == "/v1/qms/ask-procedure":
                user_id = (payload.get("user_id") or "anonymous").strip()
                question = (payload.get("question") or "").strip()
                if not question:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "question is required"})
                    return
                data = self.service.ask_process_qa(user_id=user_id, question=question)
                self._send_json(HTTPStatus.OK, {"ok": True, "data": data})
                return

            if self.path == "/v1/qms/learn-module":
                user_id = (payload.get("user_id") or "anonymous").strip()
                module = (payload.get("module") or "管理评审").strip()
                question = (payload.get("question") or "请系统讲解该模块并给实践建议").strip()
                data = self.service.ask_learning_qa(user_id=user_id, module=module, question=question)
                self._send_json(HTTPStatus.OK, {"ok": True, "data": data})
                return

            if self.path == "/v1/qms/feedback":
                user_id = (payload.get("user_id") or "anonymous").strip()
                question = (payload.get("question") or "").strip()
                original_answer = (payload.get("original_answer") or "").strip()
                corrected_answer = (payload.get("corrected_answer") or "").strip()
                note = (payload.get("note") or "").strip()
                session_id = (payload.get("session_id") or "").strip() or None
                score = payload.get("score", 1.0)

                if not question:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "question is required"})
                    return
                if not original_answer:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "original_answer is required"})
                    return
                if not corrected_answer:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "corrected_answer is required"})
                    return

                data = self.service.submit_user_feedback(
                    user_id=user_id,
                    question=question,
                    original_answer=original_answer,
                    corrected_answer=corrected_answer,
                    score=score,
                    note=note,
                    session_id=session_id,
                )
                self._send_json(HTTPStatus.OK, {"ok": True, "data": data})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(e)})


def run_server(host: str = "0.0.0.0", port: int = 9390):
    cfg = load_config_from_env()
    QMSHandler.service = QMSAgentService(cfg)
    server = ThreadingHTTPServer((host, port), QMSHandler)
    print(f"QMS Agent backend listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
