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
 * and gives up; the rover feed, control and telemetry carry on unaffected.
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

function arenaConfig() {
  var raw = (document.getElementById("arena_cameras") || {}).value || "{}";
  var cameras = {};
  try {
    cameras = JSON.parse(raw);
  } catch (e) {
    cameras = {};
  }
  return {
    appid: ((document.getElementById("arena_appid") || {}).value || "").trim(),
    token: ((document.getElementById("arena_rtc_token") || {}).value || "").trim(),
    channel: ((document.getElementById("arena_channel") || {}).value || "").trim(),
    uid: ((document.getElementById("arena_uid") || {}).value || "").trim(),
    cameras: cameras,
  };
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
  arenaSubscribeChain = arenaSubscribeChain.then(async function () {
    try {
      await arenaClient.subscribe(user, mediaType);
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

// The RTC token expires (an hour by default). Without renewal every camera
// goes black mid-competition with nothing in the logs to explain it, so pull a
// fresh one from this same server and hand it to Agora.
async function arenaRenewToken() {
  try {
    const response = await fetch("/arena/token", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const fresh = await response.json();
    if (!fresh || !fresh.RTC_TOKEN) throw new Error("no token in response");
    await arenaClient.renewToken(fresh.RTC_TOKEN);
    document.getElementById("arena_rtc_token").value = fresh.RTC_TOKEN;
    console.log("arena: token renewed");
  } catch (e) {
    arenaError = "token renewal failed: " + ((e && e.message) || e);
    console.error("arena: " + arenaError);
  }
}

async function arenaJoin() {
  const config = arenaConfig();
  if (!config.appid || !config.channel || !config.token) {
    arenaError = "arena credentials not supplied by the backend";
    console.log("arena: " + arenaError + " - skipping (rover feed unaffected)");
    window.arenaReady = true; // ready, with nothing to serve
    return;
  }

  try {
    arenaClient = AgoraRTC.createClient({ mode: "live", codec: "h264" });
    arenaClient.on("user-published", arenaHandleUserPublished);
    arenaClient.on("user-unpublished", arenaHandleUserUnpublished);
    arenaClient.on("token-privilege-will-expire", arenaRenewToken);
    arenaClient.on("token-privilege-did-expire", arenaRenewToken);

    // Audience: this client only ever watches the cameras.
    await arenaClient.setClientRole("audience");
    const joined = await arenaClient.join(
      config.appid,
      config.channel,
      config.token,
      config.uid ? Number(config.uid) : null
    );
    arenaJoined = true;
    arenaError = null;
    console.log("arena: joined " + config.channel + " as uid " + joined);
  } catch (e) {
    arenaError = (e && e.message) || String(e);
    console.error("arena: join failed - " + arenaError);
  } finally {
    window.arenaReady = true;
  }
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
