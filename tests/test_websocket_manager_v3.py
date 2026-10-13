"""C2：websocket 接收執行緒死掉或長時間沒有訊息時，必須重連並重新訂閱。"""
from unittest.mock import MagicMock, patch

import services.websocket_manager as W


def _mgr():
    m = W.WebSocketManager("k", "s", MagicMock())
    m.symbols, m.interval = ["AAAUSDT"], "1h"
    return m


def test_unhealthy_when_socket_thread_dead():
    m = _mgr()
    m.ws_client = MagicMock()
    m.ws_client.socket_manager.is_alive.return_value = False
    m.last_message_at = W.time.monotonic()
    assert not m._healthy()


def test_unhealthy_when_silent_too_long():
    m = _mgr()
    m.ws_client = MagicMock()
    m.ws_client.socket_manager.is_alive.return_value = True
    m.last_message_at = W.time.monotonic() - W.STALE_SECONDS - 1
    assert not m._healthy()
    m.last_message_at = W.time.monotonic()
    assert m._healthy()


def test_any_message_refreshes_liveness():
    m = _mgr()
    m.last_message_at = 0.0
    m._message_handler(None, '{"e":"kline","s":"AAAUSDT","k":{"x":false}}')
    assert m.last_message_at > 0


def test_run_reconnects_and_resubscribes_after_socket_dies():
    m = _mgr()
    clients, sleeps = [], []

    def factory(**kw):
        c = MagicMock()
        c.socket_manager.is_alive.return_value = len(clients) > 0      # 第一條連線的接收執行緒已死
        clients.append(c)
        return c

    def fake_sleep(_):
        sleeps.append(1)
        if len(clients) >= 2 or len(sleeps) > 50:                      # 50 次是防止舊程式無限迴圈的保險
            m.is_running = False

    m.is_running = True
    with patch.object(W, "SpotWebsocketStreamClient", side_effect=factory), \
            patch.object(W.time, "sleep", side_effect=fake_sleep):
        m._run()
    assert len(clients) == 2
    clients[1].subscribe.assert_called_once_with(stream=["aaausdt@kline_1h"])
