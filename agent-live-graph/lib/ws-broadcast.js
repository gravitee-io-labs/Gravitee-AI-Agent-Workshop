/**
 * WS Broadcast — WebSocket client management
 *
 * Manages WebSocket connections and broadcasts messages to all connected clients.
 */

'use strict';

const { WebSocketServer } = require('ws');

class WsBroadcast {
  constructor() {
    this._clients = new Set();
    this._wss = null;
  }

  /**
   * Attach WebSocket server to an existing HTTP server.
   */
  attach(httpServer) {
    this._wss = new WebSocketServer({ server: httpServer });
    this._wss.on('connection', (ws) => {
      this._clients.add(ws);
      console.log(`[WS] Client connected (${this._clients.size} total)`);
      ws.on('close', () => this._clients.delete(ws));
    });
  }

  /**
   * Broadcast a message to all connected clients.
   */
  broadcast(msg) {
    const payload = JSON.stringify(msg);
    for (const ws of this._clients) {
      if (ws.readyState === 1) ws.send(payload);
    }
  }

  get clientCount() {
    return this._clients.size;
  }
}

module.exports = { WsBroadcast };
