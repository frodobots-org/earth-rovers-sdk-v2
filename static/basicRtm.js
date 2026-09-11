$(document).ready(function () {
  window.rtmReady = false;
  const APP_ID = $("#appid").val();
  const USER_ID = $("#uid").val();
  const TOKEN = $("#rtm_token").val();
  const channelName = $("#channel").val(); // Replace with your desired channel name
  const botUid = $("#bot_uid").val();

  // Create an instance of the Agora RTM client
  const rtmClient = AgoraRTM.createInstance(APP_ID);

  // Create an instance of an RTM channel
  const rtmChannel = rtmClient.createChannel(channelName);

  // Event listener for connection state changes. NOTE: Agora RTM 1.x emits
  // "ConnectionStateChanged" (with the trailing d) — the un-suffixed name is
  // never fired and silently freezes rtmReady at its login value.
  function handleConnectionState(newState, reason) {
    window.rtmReady = newState === "CONNECTED";
    window.rtmAborted = newState === "ABORTED";
    console.log(
      "on connection state changed to " + newState + " reason: " + reason
    );
  }
  rtmClient.on("ConnectionStateChanged", handleConnectionState);
  rtmClient.on("ConnectionStateChange", handleConnectionState); // belt & braces

  // Event listener for receiving a channel message
  rtmChannel.on("ChannelMessage", () => {
    // Channel messages are not used by the SDK hot path.
  });

  rtmClient.on("MessageFromPeer", function (message, peerId) {
    // An SDK RTM user can receive direct messages from more than one peer.
    // Only the rover selected by the auth response is a telemetry source.
    if (String(peerId) !== String(botUid)) return;

    let controls;
    try {
      controls = JSON.parse(new TextDecoder().decode(message.rawMessage));
    } catch (_) {
      return;
    }
    if (!controls || typeof controls !== "object") return;
    const event = new CustomEvent("message-from-peer", { detail: controls });
    window.rtm_data = controls;
    window.rtm_data.timestamp = Date.now() / 1000;
    document.dispatchEvent(event);

    if (
      controls.latitude !== null &&
      controls.latitude !== undefined &&
      controls.longitude !== null &&
      controls.longitude !== undefined
    ) {
      const latitude = parseFloat(controls.latitude);
      const longitude = parseFloat(controls.longitude);
      window.updateMarker(latitude, longitude);
    }
  });

  // Function to join the RTM channel
  function joinRTMChannel(uid) {
    return rtmClient
      .login({ token: TOKEN, uid: String(uid) })
      .then(() => {
        console.log("AgoraRTM client login success");
        return rtmChannel
          .join()
          .then(() => {
            console.log("RTM Channel join success");
            window.rtmReady = true;
          })
          .catch((error) => {
            console.log("Failed to join channel for error: " + error);
          });
      })
      .catch((err) => {
        console.log("AgoraRTM client login failure", err);
      });
  }

  // --- Sending commands to the rover ---
  const SEND_TIMEOUT_MS = 4000;
  const UNREACHABLE_MSG =
    "Rover unreachable - it may be offline or the mission has ended";

  // Delivery observability: /status reads this via the browser service.
  window.rtmStats = {
    dispatched: 0,
    delivered: 0,
    failed: 0,
    unconfirmed: 0,
    lastError: null,
    lastDeliveredAt: null,
  };

  // Dispatch immediately and track the outcome asynchronously. Resolves true
  // only when Agora confirms the PEER received it (hasPeerReceived).
  function dispatchToRover(json) {
    if (!window.rtmReady) {
      window.rtmStats.failed += 1;
      window.rtmStats.lastError = "RTM client is not connected";
      throw new Error("RTM client is not connected");
    }
    const message = JSON.stringify(json);
    window.rtmStats.dispatched += 1;
    const result = rtmClient
      .sendMessageToPeer({ text: message }, botUid)
      .then((sendResult) => {
        // RTSA peers can accept a command without emitting a peer receipt.
        // Preserve the distinction from a rejected transport Promise: safety
        // sends may then verify fresh stopped-wheel telemetry, never liveness.
        if (!sendResult || typeof sendResult.hasPeerReceived !== "boolean") {
          throw new Error("RTM send returned no delivery result");
        }
        if (!sendResult.hasPeerReceived) {
          window.rtmStats.unconfirmed += 1;
          return false;
        }
        window.rtmStats.delivered += 1;
        window.rtmStats.lastError = null;
        window.rtmStats.lastDeliveredAt = Date.now() / 1000;
        return true;
      })
      .catch((err) => {
        window.rtmStats.failed += 1;
        window.rtmStats.lastError = String((err && err.message) || err);
        throw err;
      });
    result.catch(() => undefined); // observed via rtmStats; no unhandled spam
    return result;
  }

  // Non-blocking send: returns as soon as the message is dispatched, so a
  // control stream is never held behind a slow peer acknowledgement.
  window.sendMessage = function (json) {
    dispatchToRover(json); // throws synchronously only when RTM is down
    return true;
  };

  // Confirmed send for safety-critical messages (watchdog stops): resolves
  // true only on an explicit peer receipt, false on server acceptance without
  // a receipt, and rejects on transport failure or timeout.
  window.sendMessageAwait = function (json) {
    const send = dispatchToRover(json);
    let timer;
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(UNREACHABLE_MSG)),
        SEND_TIMEOUT_MS
      );
    });
    return Promise.race([send, deadline])
      .catch((err) => {
        const text = String((err && err.message) || err);
        throw /timed out|timeout/i.test(text) ? new Error(UNREACHABLE_MSG) : err;
      })
      .finally(() => clearTimeout(timer));
  };

  // RTM health snapshot for the server.
  window.rtmHealth = function () {
    return {
      ready: window.rtmReady === true,
      aborted: window.rtmAborted === true,
      dispatched: window.rtmStats.dispatched,
      delivered: window.rtmStats.delivered,
      failed: window.rtmStats.failed,
      unconfirmed: window.rtmStats.unconfirmed,
      last_error: window.rtmStats.lastError,
      last_delivered_at: window.rtmStats.lastDeliveredAt,
    };
  };

  // Call joinRTMChannel with the user ID to start the process
  joinRTMChannel(USER_ID);
});
