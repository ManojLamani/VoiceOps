import { useEffect, useRef } from 'react';
import api from '../api/api';

/**
 * Subscribe to a conversation's live events while `enabled`: READY, MESSAGE, HUMAN_JOINED, HUMAN_LEFT and
 * CONVERSATION_ENDED. Reconnects with backoff until the conversation ends or the component unmounts.
 */
export default function useLiveConversation(conversationId, enabled, onEvent) {
  const handler = useRef(onEvent);
  useEffect(() => { handler.current = onEvent; }, [onEvent]);

  useEffect(() => {
    if (!enabled || !conversationId) return undefined;
    let ws = null;
    let timer = null;
    let stopped = false;
    let attempts = 0;

    const open = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${protocol}://${window.location.host}/api/conversations/${conversationId}/live`);
      ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: api.getToken() }));
      ws.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch { return; }
        if (msg.type === 'READY') attempts = 0;
        if (['CONVERSATION_ENDED', 'AUTH_FAILED', 'ERROR'].includes(msg.type)) stopped = true;
        handler.current(msg);
      };
      ws.onclose = () => {
        if (stopped) return;
        attempts += 1;
        timer = setTimeout(open, Math.min(10000, 1000 * 2 ** attempts));
      };
    };
    open();
    return () => { stopped = true; clearTimeout(timer); ws?.close(); };
  }, [conversationId, enabled]);
}
