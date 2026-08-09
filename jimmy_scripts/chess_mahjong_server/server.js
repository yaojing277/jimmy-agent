const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const path = require('path');

const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static(path.join(__dirname, 'public')));

const rooms = {};

// ── 遊戲邏輯 ─────────────────────────────────────────────────────────────────

function createDeck() {
  const pieces = [];
  let id = 0;
  const redTypes   = [['帥',1],['仕',2],['相',2],['車',2],['馬',2],['炮',2],['兵',5]];
  const blackTypes = [['將',1],['士',2],['象',2],['車',2],['馬',2],['包',2],['卒',5]];
  for (const [type, n] of redTypes)   for (let i=0;i<n;i++) pieces.push({id:id++,type,color:'red'});
  for (const [type, n] of blackTypes) for (let i=0;i<n;i++) pieces.push({id:id++,type,color:'black'});
  return pieces;
}

function shuffle(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

const TYPE_ORDER  = { '帥':0,'將':0,'仕':1,'士':1,'相':2,'象':2,'車':3,'馬':4,'炮':5,'包':5,'兵':6,'卒':6 };
const COLOR_ORDER = { red: 0, black: 1 };

function sortHand(hand) {
  hand.sort((a, b) =>
    COLOR_ORDER[a.color] - COLOR_ORDER[b.color] ||
    (TYPE_ORDER[a.type] ?? 9) - (TYPE_ORDER[b.type] ?? 9)
  );
}

function pairKey(type) { return type === '帥' ? '將' : type; }
function isPair(two)    { return two.length === 2 && pairKey(two[0].type) === pairKey(two[1].type); }

function combos(arr, k) {
  if (k === 0) return [[]];
  if (arr.length < k) return [];
  const [h, ...t] = arr;
  return [...combos(t, k - 1).map(c => [h, ...c]), ...combos(t, k)];
}

function checkWin(hand) {
  if (hand.length !== 5) return { win: false };

  const trioRules = [
    { color: 'red',   types: ['帥','仕','相'], label: '紅帥+仕+相' },
    { color: 'black', types: ['將','士','象'], label: '黑將+士+象' },
    { color: 'red',   types: ['車','馬','炮'], label: '紅車+馬+炮' },
    { color: 'black', types: ['車','馬','包'], label: '黑車+馬+包' },
  ];
  for (const { color, types, label } of trioRules) {
    const trio = types.map(t => hand.find(p => p.type === t && p.color === color));
    if (trio.some(p => !p)) continue;
    const ids  = new Set(trio.map(p => p.id));
    const rest = hand.filter(p => !ids.has(p.id));
    if (isPair(rest)) return { win: true, pattern: `${label} + 對子（${rest[0].type}）` };
  }

  for (const [type, color, label] of [['卒','black','黑卒'],['兵','red','紅兵']]) {
    const pawns = hand.filter(p => p.type === type && p.color === color);
    if (pawns.length === 5) return { win: true, pattern: `五${label}` };
    if (pawns.length >= 3) {
      for (const trio of combos(pawns, 3)) {
        const ids  = new Set(trio.map(p => p.id));
        const rest = hand.filter(p => !ids.has(p.id));
        if (isPair(rest)) return { win: true, pattern: `三${label} + 對子（${rest[0].type}）` };
      }
    }
  }
  return { win: false };
}

function generateRoomId() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let id;
  do { id = Array.from({ length: 4 }, () => chars[Math.floor(Math.random() * chars.length)]).join(''); }
  while (rooms[id]);
  return id;
}

function addLog(room, msg) {
  const t = new Date().toLocaleTimeString('zh-TW');
  room.log.push(`[${t}] ${msg}`);
  if (room.log.length > 60) room.log.shift();
}

// 對每位玩家產生專屬視角（只看到自己手牌，結局時全部揭開）
function buildState(room, playerIndex, revealAll = false) {
  return {
    phase: room.phase,
    currentPlayer: room.currentPlayer,
    deckCount: room.deck.length,
    drawnPieceId: room.drawnPieceId,
    lastDiscard: room.lastDiscard,
    lastDiscardPlayer: room.lastDiscardPlayer,
    myIndex: playerIndex,
    players: room.players.map((p, idx) => ({
      id: idx,
      name: p.name,
      isDealer: p.isDealer,
      online: !!p.socketId,
      handCount: p.hand.length,
      hand: (idx === playerIndex || revealAll)
        ? p.hand
        : p.hand.map(() => ({ hidden: true })),
    })),
    discards: room.discards,
    log: room.log,
  };
}

function broadcastGame(room, revealAll = false) {
  room.players.forEach((p, idx) => {
    if (p.socketId) io.to(p.socketId).emit('game_update', buildState(room, idx, revealAll));
  });
}

function lobbyState(room) {
  return {
    roomId: room.id,
    phase: room.phase,
    players: room.players.map((p, idx) => ({ id: idx, name: p.name, online: !!p.socketId })),
  };
}

function broadcastLobby(room) {
  io.to(room.id).emit('lobby_update', lobbyState(room));
}

function startGame(room) {
  const deck = shuffle(createDeck());
  room.deck = deck;
  room.discards = [[], [], [], []];
  room.phase = 'draw';
  room.drawnPieceId = null;
  room.lastDiscard = null;
  room.lastDiscardPlayer = -1;
  room.log = [];

  room.players.forEach((p, idx) => {
    p.hand = [];
    p.isDealer = idx === room.dealerIndex;
  });
  room.currentPlayer = room.dealerIndex;
  room.dealerIndex = (room.dealerIndex + 1) % room.players.length;

  for (let i = 0; i < 4; i++) {
    for (const p of room.players) p.hand.push(deck.pop());
  }
  room.players.forEach(p => sortHand(p.hand));

  addLog(room, `遊戲開始！「${room.players[room.currentPlayer].name}」為莊家，請摸牌。`);
  broadcastGame(room);
}

// ── Socket.io ─────────────────────────────────────────────────────────────────

io.on('connection', (socket) => {

  socket.on('create_room', ({ playerName }) => {
    const name = (playerName || '玩家').trim() || '玩家';
    const roomId = generateRoomId();
    rooms[roomId] = {
      id: roomId,
      players: [{ socketId: socket.id, name, hand: [], isDealer: false }],
      discards: [[], [], [], []],
      deck: [],
      currentPlayer: 0,
      phase: 'waiting',
      drawnPieceId: null,
      lastDiscard: null,
      lastDiscardPlayer: -1,
      dealerIndex: 0,
      log: [],
    };
    socket.join(roomId);
    socket.roomId = roomId;
    socket.playerIndex = 0;
    socket.emit('joined', { roomId, playerIndex: 0, isHost: true });
    broadcastLobby(rooms[roomId]);
  });

  socket.on('join_room', ({ roomId, playerName }) => {
    const rid = (roomId || '').toUpperCase().trim();
    const room = rooms[rid];
    if (!room) return socket.emit('error', { message: '找不到房間，請確認代碼' });
    if (room.phase !== 'waiting') return socket.emit('error', { message: '遊戲已開始，無法加入' });
    if (room.players.length >= 4) return socket.emit('error', { message: '房間已滿（最多 4 人）' });

    const name = (playerName || `玩家 ${room.players.length + 1}`).trim();
    const playerIndex = room.players.length;
    room.players.push({ socketId: socket.id, name, hand: [], isDealer: false });
    socket.join(rid);
    socket.roomId = rid;
    socket.playerIndex = playerIndex;
    socket.emit('joined', { roomId: rid, playerIndex, isHost: false });
    broadcastLobby(room);
  });

  socket.on('start_game', () => {
    const room = rooms[socket.roomId];
    if (!room || room.phase !== 'waiting') return;
    if (socket.playerIndex !== 0) return socket.emit('error', { message: '只有房主可以開始遊戲' });
    if (room.players.length < 2) return socket.emit('error', { message: '至少需要 2 位玩家才能開始' });
    startGame(room);
  });

  socket.on('draw', () => {
    const room = rooms[socket.roomId];
    if (!room || room.phase !== 'draw' || socket.playerIndex !== room.currentPlayer) return;

    if (room.deck.length === 0) {
      addLog(room, '牌堆已空，本局平局！');
      room.phase = 'idle';
      broadcastGame(room, true);
      return;
    }

    const piece = room.deck.pop();
    const player = room.players[room.currentPlayer];
    player.hand.push(piece);
    sortHand(player.hand);
    room.drawnPieceId = piece.id;
    room.phase = 'discard';
    addLog(room, `「${player.name}」摸牌，請打出一張`);

    const result = checkWin(player.hand);
    if (result.win) {
      addLog(room, `「${player.name}」胡牌！${result.pattern}`);
      room.phase = 'idle';
      broadcastGame(room, true);
      io.to(room.id).emit('win', { playerIndex: room.currentPlayer, playerName: player.name, pattern: result.pattern });
    } else {
      broadcastGame(room);
    }
  });

  socket.on('discard', ({ pieceId }) => {
    const room = rooms[socket.roomId];
    if (!room || room.phase !== 'discard' || socket.playerIndex !== room.currentPlayer) return;

    const playerId = room.currentPlayer;
    const player   = room.players[playerId];
    const idx = player.hand.findIndex(p => p.id === pieceId);
    if (idx === -1) return;

    const discarded = player.hand.splice(idx, 1)[0];
    room.discards[playerId].push(discarded);
    room.drawnPieceId = null;
    room.lastDiscard = discarded;
    room.lastDiscardPlayer = playerId;
    addLog(room, `「${player.name}」打出「${discarded.color === 'red' ? '紅' : '黑'}${discarded.type}」`);

    // 放砲檢查
    for (let i = 1; i <= room.players.length - 1; i++) {
      const checkId = (playerId + i) % room.players.length;
      const cp = room.players[checkId];
      const result = checkWin([...cp.hand, discarded]);
      if (result.win) {
        room.discards[playerId].splice(room.discards[playerId].findIndex(p => p.id === discarded.id), 1);
        cp.hand.push(discarded);
        sortHand(cp.hand);
        addLog(room, `「${cp.name}」以「${result.pattern}」胡牌（放砲）！`);
        room.phase = 'idle';
        broadcastGame(room, true);
        io.to(room.id).emit('win', { playerIndex: checkId, playerName: cp.name, pattern: result.pattern });
        return;
      }
    }

    room.currentPlayer = (playerId + 1) % room.players.length;
    room.phase = 'can-eat';
    broadcastGame(room);
  });

  socket.on('eat', () => {
    const room = rooms[socket.roomId];
    if (!room || room.phase !== 'can-eat' || socket.playerIndex !== room.currentPlayer || !room.lastDiscard) return;

    const piece = room.lastDiscard;
    const pile  = room.discards[room.lastDiscardPlayer];
    const idx   = pile.findIndex(p => p.id === piece.id);
    if (idx !== -1) pile.splice(idx, 1);

    const player = room.players[room.currentPlayer];
    player.hand.push(piece);
    sortHand(player.hand);
    room.drawnPieceId = piece.id;
    room.lastDiscard = null;
    room.lastDiscardPlayer = -1;
    room.phase = 'discard';
    addLog(room, `「${player.name}」吃了「${piece.color === 'red' ? '紅' : '黑'}${piece.type}」，請打出一張`);

    const result = checkWin(player.hand);
    if (result.win) {
      addLog(room, `「${player.name}」胡牌！${result.pattern}`);
      room.phase = 'idle';
      broadcastGame(room, true);
      io.to(room.id).emit('win', { playerIndex: room.currentPlayer, playerName: player.name, pattern: result.pattern });
    } else {
      broadcastGame(room);
    }
  });

  socket.on('skip_eat', () => {
    const room = rooms[socket.roomId];
    if (!room || room.phase !== 'can-eat' || socket.playerIndex !== room.currentPlayer) return;
    room.lastDiscard = null;
    room.lastDiscardPlayer = -1;
    room.phase = 'draw';
    broadcastGame(room);
  });

  socket.on('new_game', () => {
    const room = rooms[socket.roomId];
    if (!room || socket.playerIndex !== 0) return;
    startGame(room);
  });

  socket.on('disconnect', () => {
    const room = rooms[socket.roomId];
    if (!room) return;
    const player = room.players[socket.playerIndex];
    if (!player) return;
    player.socketId = null;
    addLog(room, `「${player.name}」已離線`);
    if (room.phase === 'waiting') {
      broadcastLobby(room);
    } else {
      broadcastGame(room);
    }
    setTimeout(() => {
      if (rooms[room.id] && room.players.every(p => !p.socketId)) delete rooms[room.id];
    }, 30000);
  });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => console.log(`象棋麻將伺服器啟動：http://localhost:${PORT}`));
