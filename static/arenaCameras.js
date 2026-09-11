/*
 * Arena ceiling cameras (GMU venue) - a SECOND Agora client on this page.
 *
 * An AgoraRTC client is bound to one App ID, and the cameras live in the
 * venue's own Agora project, so they cannot travel through the rover client in
 * basicVideoCall.js. This file keeps its own client and its own remote-user
 * map: the rover's `remoteUsers` is keyed by bare UID, so a camera UID could
 * otherwise collide with a rover UID and cross the two feeds.
 *
 * Nothing here touches the rover client. If the arena is down, this file logs
 * and retries independently; the rover feed, control and telemetry carry on unaffected.
 */

var arenaClient = null;
var arenaRemoteUsers = {};
var arenaError = null;
var arenaJoined = false;
window.arenaReady = false;

// Subscribes are serialized. Six cameras publishing at once means six
// near-simultaneous WebRTC negotiations, which the transport does not
// reliably survive - the rover client learned the same lesson.
var arenaSubscribeChain = Promise.resolve();

// Credentials are fetched after the page loads, never while rendering the
// rover's /sdk page. An arena outage cannot delay rover RTM initialization.
var arenaConfiguration = { appid: "", token: "", channel: "", uid: "", cameras: {} };
var arenaOperation = null;
var arenaRetryTimer = null;
var arenaRetryDelay = 1000;
var arenaNeedsRejoin = false;

function arenaConfig() {
  return arenaConfiguration;
}

function arenaCamForUid(uid) {
  var cameras = arenaConfig().cameras;
  var found = null;
  Object.keys(cameras).forEach(function (cam) {
    if (String(cameras[cam]) === String(uid)) found = Number(cam);
  });
  return found;
}

// Agora only decodes frames for a track that is actually being rendered:
// getCurrentFrameData() on an unplayed track yields nothing. So each camera
// gets a small player element, exactly as the rover feed does.
function arenaPlayerFor(uid) {
  var list = document.getElementById("arena-playerlist");
  if (!list) return null;
  var id = "arena-player-" + uid;
  if (!document.getElementById(id)) {
    var el = document.createElement("div");
    el.id = id;
    el.style.width = "240px";
    el.style.height = "180px";
    list.appendChild(el);
  }
  return id;
}

function arenaRemovePlayer(uid) {
  var el = document.getElementById("arena-player-" + uid);
  if (el) el.remove();
}

async function arenaSubscribe(user, mediaType) {
  const client = arenaClient;
  arenaSubscribeChain = arenaSubscribeChain.then(async function () {
    try {
      if (client !== arenaClient || arenaRemoteUsers[user.uid] !== user) return;
      await client.subscribe(user, mediaType);
      if (client !== arenaClient || arenaRemoteUsers[user.uid] !== user) return;
      var playerId = arenaPlayerFor(user.uid);
      if (playerId && user.videoTrack) {
        user.videoTrack.play(playerId);
        // Same handshake the rover client uses: captureEnabled is our own
        // flag, not an Agora property, marking a track as played and
        // therefore safe to capture from.
        user.videoTrack.captureEnabled = true;
      }
      console.log("arena: subscribed to uid " + user.uid + " " + mediaType);
    } catch (e) {
      console.error("arena: subscribe failed for uid " + user.uid, e);
    }
  });
  return arenaSubscribeChain;
}

// Only video is subscribed. These are ceiling cameras watched by models, and
// six unused audio tracks would decode for nothing.
function arenaHandleUserPublished(user, mediaType) {
  if (mediaType !== "video") return;
  arenaRemoteUsers[user.uid] = user;
  arenaSubscribe(user, mediaType);
}

function arenaHandleUserUnpublished(user, mediaType) {
  if (mediaType !== "video") return;
  delete arenaRemoteUsers[user.uid];
  arenaRemovePlayer(user.uid);
}

const arenaCaptureSurfaces = new WeakMap();

async function arenaCapture(videoTrack, imageFormat, imageQuality) {
  const frame = await videoTrack.getCurrentFrameData();
  if (!frame || !frame.width) return null;
  let surface = arenaCaptureSurfaces.get(videoTrack);
  if (!surface) {
    const canvas = document.createElement("canvas");
    surface = { canvas: canvas, context: canvas.getContext("2d") };
    arenaCaptureSurfaces.set(videoTrack, surface);
  }
  if (surface.canvas.width !== frame.width) surface.canvas.width = frame.width;
  if (surface.canvas.height !== frame.height) surface.canvas.height = frame.height;
  surface.context.putImageData(frame, 0, 0);
  return surface.canvas.toDataURL(
    "image/" + (imageFormat || "jpeg"),
    imageQuality === undefined ? 0.8 : imageQuality
  );
}

// Same contract as the rover's getFramePacket: {data_url, timestamp}, null
// when that camera is not publishing, or {error} when a frame exists but
// cannot be decoded.
async function getArenaFramePacket(uid, imageFormat, imageQuality) {
  if (!arenaJoined) return { error: arenaError || "Arena cameras are reconnecting" };
  const user = arenaRemoteUsers[uid];
  if (!user || !user.videoTrack || !user.videoTrack.captureEnabled) {
    return null;
  }
  const capturedAt = Date.now() / 1000;
  try {
    const dataUrl = await arenaCapture(user.videoTrack, imageFormat, imageQuality);
    if (!dataUrl) return null;
    return { data_url: dataUrl, timestamp: capturedAt };
  } catch (err) {
    const text = String((err && err.message) || err);
    if (/IndexSizeError|source (width|height) is 0/i.test(text)) {
      return {
        error:
          "arena camera " + uid + " is publishing but no frames are decoding (0x0); " +
          "the browser likely lacks the H.264 codec - install Google Chrome " +
          "or set CHROME_EXECUTABLE_PATH to a codec-capable browser",
      };
    }
    return { error: text };
  }
}

function arenaStatus() {
  var cameras = arenaConfig().cameras;
  var live = Object.keys(arenaRemoteUsers).map(Number);
  return {
    joined: arenaJoined,
    error: arenaError,
    channel: arenaConfig().channel,
    live_uids: live,
    cameras: Object.keys(cameras).map(function (cam) {
      return {
        cam: Number(cam),
        uid: cameras[cam],
        online: live.indexOf(Number(cameras[cam])) !== -1,
      };
    }),
    unmapped_uids: live.filter(function (uid) {
      return arenaCamForUid(uid) === null;
    }),
  };
}

function arenaScheduleRetry() {
  if (arenaRetryTimer !== null) return;
  arenaRetryTimer = setTimeout(function () {
    arenaRetryTimer = null;
    arenaJoin(true);
  }, arenaRetryDelay);
  arenaRetryDelay = Math.min(arenaRetryDelay * 2, 30000);
}

async function arenaFetchConfig() {
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(), 18000);
  try {
    const response = await fetch("/arena/token", {
      cache: "no-store", signal: abort.signal,
    });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const fresh = await response.json();
    if (!fresh || !fresh.APP_ID || !fresh.CHANNEL_NAME || !fresh.RTC_TOKEN) {
      throw new Error("arena credentials not supplied by the backend");
    }
    return {
      appid: fresh.APP_ID, token: fresh.RTC_TOKEN, channel: fresh.CHANNEL_NAME,
      uid: String(fresh.USERID || ""), cameras: fresh.CAMERAS || {},
    };
  } finally {
    clearTimeout(timeout);
  }
}

function arenaRenewToken() {
  return arenaJoin(true);
}

function arenaJoin(refresh = false) {
  if (arenaOperation) return arenaOperation;
  if (arenaJoined && !refresh) return Promise.resolve();
  if (arenaRetryTimer !== null) clearTimeout(arenaRetryTimer);
  arenaRetryTimer = null;
  arenaOperation = (async function () {
    try {
      const config = await arenaFetchConfig();
      const old = arenaConfig();
      if (arenaClient && arenaJoined && !arenaNeedsRejoin &&
          arenaClient.connectionState === "CONNECTED" &&
          old.appid === config.appid && old.channel === config.channel &&
          old.uid === config.uid) {
        await arenaClient.renewToken(config.token);
        arenaConfiguration = config;
      } else {
        // Expired tokens or a changed identity/channel need a fresh join.
        // Tear down only the arena client; never reset the rover's page.
        const previous = arenaClient;
        arenaClient = null;
        arenaJoined = false;
        arenaNeedsRejoin = false;
        if (previous) {
          previous.removeAllListeners();
          await previous.leave();
        }
        Object.keys(arenaRemoteUsers).forEach(arenaRemovePlayer);
        arenaRemoteUsers = {};
        arenaSubscribeChain = Promise.resolve();
        arenaConfiguration = config;
        const client = AgoraRTC.createClient({ mode: "live", codec: "h264" });
        arenaClient = client;
        client.on("user-published", arenaHandleUserPublished);
        client.on("user-unpublished", arenaHandleUserUnpublished);
        client.on("user-left", user => arenaHandleUserUnpublished(user, "video"));
        client.on("token-privilege-will-expire", arenaRenewToken);
        client.on("token-privilege-did-expire", function () {
          arenaNeedsRejoin = true;
          arenaJoined = false;
          arenaScheduleRetry();
        });
        client.on("connection-state-change", function (state) {
          if (client !== arenaClient) return;
          if (state === "DISCONNECTED") {
            arenaJoined = false;
            arenaScheduleRetry();
          }
        });
        await client.setClientRole("audience");
        await client.join(config.appid, config.channel, config.token,
                          config.uid ? Number(config.uid) : null);
        arenaJoined = !arenaNeedsRejoin;
      }
      arenaError = null;
      arenaRetryDelay = 1000;
    } catch (e) {
      arenaError = (e && e.message) || String(e);
      console.error("arena: " + arenaError);
      arenaScheduleRetry();
    } finally {
      window.arenaReady = true;
      arenaOperation = null;
      if (arenaNeedsRejoin) arenaScheduleRetry();
    }
  })();
  return arenaOperation;
}

window.getArenaFramePacket = getArenaFramePacket;
window.arenaStatus = arenaStatus;
window.arenaJoin = arenaJoin;

// Joins on its own rather than waiting for the rover's #join click: the arena
// is independent of whether a rover ride is live.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", arenaJoin);
} else {
  arenaJoin();
}
