// Dashboard logic: spectator video, realtime telemetry, compass, map wiring,
// drive controls, speak, checkpoints. Everything degrades gracefully when the
// rover or the SDK pipeline isn't up yet.
(function () {
  var DASH = window.DASH || {};
  var $ = function (id) {
    return document.getElementById(id);
  };

  // Every same-origin API call this page makes needs the shared secret this
  // page itself was loaded with (see GET / in main.py).
  var AUTH_HEADER = { Authorization: "Bearer " + (DASH.apiKey || "") };
  function apiFetch(url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, AUTH_HEADER, opts.headers || {});
    return fetch(url, opts);
  }

  /* ---------- Header / mission lifecycle ---------- */

  if (DASH.botSlug) $("bot-chip").textContent = DASH.botSlug;
  if (DASH.missionSlug) $("mission-chip").textContent = DASH.missionSlug;

  function showBootNotice(message, isError) {
    var notice = $("boot-notice");
    notice.textContent = message || "";
    notice.classList.toggle("hidden", !message);
    notice.classList.toggle("error", !!isError);
  }

  // Empty-state message centered in the video area (title + hint + optional
  // Start mission button). Used whenever no video is playing.
  function setPlaceholder(title, opts) {
    opts = opts || {};
    var titleEl = $("ph-title");
    titleEl.textContent = title;
    titleEl.classList.toggle("warn", opts.tone === "warn");
    titleEl.classList.toggle("error", opts.tone === "error");
    var sub = $("ph-sub");
    sub.textContent = opts.sub || "";
    sub.classList.toggle("hidden", !opts.sub);
    $("ph-mission-btn").classList.toggle("hidden", !opts.startButton);
  }

  function videoActive() {
    return $("front-placeholder").classList.contains("hidden");
  }

  // Guidance under an error title. "Fix the issue and try again" is wrong when
  // nothing on this side can be fixed (e.g. someone else is driving the bot),
  // so map those cases to a "wait" message.
  function problemSubtitle(message) {
    if (message === "Bot unavailable for SDK") {
      return "Another user is using this bot — try again shortly.";
    }
    return "Fix the issue and try again.";
  }

  // Problems go where the user is looking: big and centered when the video
  // area is empty, a slim overlay strip when video is playing.
  function reportProblem(message, isError) {
    if (videoActive()) {
      showBootNotice(message, isError);
    } else {
      showBootNotice("", false);
      setPlaceholder(message, {
        tone: isError ? "error" : "warn",
        sub: isError ? problemSubtitle(message) : "",
        startButton: !missionStarted && !!DASH.missionSlug,
      });
    }
  }

  var missionAction = $("mission-action-btn");
  var missionStarted = !!DASH.missionStarted;

  if (DASH.bootNotice) {
    if (/start-mission/i.test(DASH.bootNotice)) {
      setPlaceholder("No mission running", {
        sub: "Start a mission to connect to the rover and watch the live feed.",
        startButton: !!DASH.missionSlug,
      });
    } else {
      reportProblem(DASH.bootNotice, true);
    }
  }

  function renderMissionAction() {
    if (!DASH.missionSlug) return;
    missionAction.classList.remove("hidden", "start", "stop");
    missionAction.classList.add(missionStarted ? "stop" : "start");
    missionAction.textContent = missionStarted ? "Stop mission" : "Start mission";
  }

  function responseError(response) {
    return response
      .json()
      .catch(function () {
        return {};
      })
      .then(function (body) {
        throw new Error(body.detail || "Request failed (" + response.status + ")");
      });
  }

  $("ph-mission-btn").addEventListener("click", function () {
    missionAction.click();
  });

  missionAction.addEventListener("click", function () {
    var stopping = missionStarted;
    if (stopping && !window.confirm("Stop the active mission?")) return;

    missionAction.disabled = true;
    missionAction.textContent = stopping ? "Stopping…" : "Starting…";
    showBootNotice("", false);
    if (!stopping) {
      setPlaceholder("Starting mission…", {
        sub: "Connecting to the rover — this can take a few seconds.",
      });
      $("ph-mission-btn").disabled = true;
    }

    var request;
    if (stopping) {
      // Dispatch a confirmed zero command before ending the remote ride.
      request = apiFetch("/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: { linear: 0, angular: 0 } }),
      })
        .catch(function () {})
        .then(function () {
          return apiFetch("/end-mission", { method: "POST" });
        });
    } else {
      request = apiFetch("/start-mission", { method: "POST" });
    }

    request
      .then(function (response) {
        if (!response.ok) return responseError(response);
        window.location.reload();
      })
      .catch(function (error) {
        $("ph-mission-btn").disabled = false;
        reportProblem(String(error.message || error), true);
        missionAction.disabled = false;
        renderMissionAction();
      });
  });

  renderMissionAction();

  if (!missionStarted) {
    document.querySelectorAll(".pad, #lamp-btn, #speak-btn, #checkpoint-btn").forEach(function (control) {
      control.disabled = true;
    });
  }

  function setLed(id, state) {
    var led = $(id);
    led.classList.remove("on", "bad");
    if (state === "on") led.classList.add("on");
    if (state === "bad") led.classList.add("bad");
  }

  /* ---------- Compass ---------- */

  var compassNeedle = null;
  var compassCumulative = 0;
  var CARDINALS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
  ];

  function buildCompass() {
    var svg = $("compass-svg");
    var NS = "http://www.w3.org/2000/svg";
    var parts = [];

    parts.push(
      '<circle cx="100" cy="100" r="94" fill="#1e2733" stroke="#2b3644" stroke-width="2"/>'
    );
    for (var deg = 0; deg < 360; deg += 10) {
      var major = deg % 90 === 0;
      var mid = !major && deg % 30 === 0;
      var len = major ? 14 : mid ? 10 : 5;
      var rad = ((deg - 90) * Math.PI) / 180;
      var x1 = 100 + Math.cos(rad) * 94;
      var y1 = 100 + Math.sin(rad) * 94;
      var x2 = 100 + Math.cos(rad) * (94 - len);
      var y2 = 100 + Math.sin(rad) * (94 - len);
      parts.push(
        '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
          '" stroke="' + (major ? "#93a4b3" : "#3a4654") +
          '" stroke-width="' + (major ? 2 : 1) + '"/>'
      );
    }
    var labels = { N: [100, 32], E: [168, 104], S: [100, 176], W: [32, 104] };
    Object.keys(labels).forEach(function (letter) {
      parts.push(
        '<text x="' + labels[letter][0] + '" y="' + labels[letter][1] +
          '" text-anchor="middle" font-size="15" font-weight="600" font-family="ui-monospace, Menlo, monospace" fill="' +
          (letter === "N" ? "#ffb224" : "#93a4b3") + '">' + letter + "</text>"
      );
    });
    parts.push(
      '<g id="compass-needle">' +
        '<path d="M100 26 L107 100 L100 96 L93 100 Z" fill="#ffb224"/>' +
        '<path d="M100 174 L107 100 L100 104 L93 100 Z" fill="#3a4654"/>' +
        '<circle cx="100" cy="100" r="5" fill="#e6edf3"/>' +
        "</g>"
    );
    svg.innerHTML = parts.join("");
    compassNeedle = svg.querySelector("#compass-needle");
    compassNeedle.style.transformOrigin = "100px 100px";
    compassNeedle.style.transition = "transform 0.15s ease-out";
  }

  function updateCompass(deg) {
    if (deg === null || deg === undefined || isNaN(deg)) return;
    deg = ((deg % 360) + 360) % 360;
    // Rotate via the shortest path so 359° -> 1° doesn't spin backwards.
    var current = ((compassCumulative % 360) + 360) % 360;
    var delta = ((deg - current + 540) % 360) - 180;
    compassCumulative += delta;
    if (compassNeedle) {
      compassNeedle.style.transform = "rotate(" + compassCumulative + "deg)";
    }
    $("compass-degrees").textContent = Math.round(deg) + "°";
    $("compass-cardinal").textContent =
      CARDINALS[Math.round(deg / 22.5) % 16];
  }

  buildCompass();

  /* ---------- Map ---------- */

  window.DashMap.init({
    checkpoints: DASH.checkpointsList || [],
    zoom: DASH.mapZoomLevel,
  });

  $("follow-switch").addEventListener("change", function () {
    window.DashMap.setFollow(this.checked);
  });
  $("radius-switch").addEventListener("change", function () {
    window.DashMap.setRadiusVisible(this.checked);
  });
  window.onFollowChanged = function (value) {
    $("follow-switch").checked = value;
  };

  /* ---------- Telemetry rendering ---------- */

  var lastDataAt = null; // local receipt time (ms)
  var lampState = 0;

  function fmt(value, digits) {
    if (value === null || value === undefined || value === "") return "—";
    var n = parseFloat(value);
    if (isNaN(n)) return String(value);
    return n.toFixed(digits === undefined ? 1 : digits);
  }

  function renderTelemetry(data) {
    if (!data) return;
    lastDataAt = Date.now();

    $("t-battery").textContent = fmt(data.battery, 0);
    var batt = parseFloat(data.battery);
    if (!isNaN(batt)) {
      var fill = $("battery-fill");
      fill.style.width = Math.max(0, Math.min(100, batt)) + "%";
      fill.style.background =
        batt <= 15 ? "var(--bad)" : batt <= 30 ? "var(--warn)" : "var(--ok)";
    }

    $("t-speed").textContent = fmt(data.speed);

    var signal = parseInt(data.signal_level, 10);
    $("t-signal").textContent = isNaN(signal) ? "—" : signal;
    var bars = $("sig-bars").children;
    for (var i = 0; i < bars.length; i++) {
      bars[i].classList.toggle("on", !isNaN(signal) && i < signal);
    }

    $("t-gps").textContent = fmt(data.gps_signal, 0);
    $("t-vibration").textContent = fmt(data.vibration, 2);

    if (data.lamp !== undefined && data.lamp !== null) {
      lampState = parseInt(data.lamp, 10) || 0;
      $("t-lamp").textContent = lampState ? "On" : "Off";
      $("lamp-btn").textContent = lampState ? "Lamp off" : "Lamp on";
      $("lamp-btn").classList.toggle("on", !!lampState);
    }

    var lat = parseFloat(data.latitude);
    var lng = parseFloat(data.longitude);
    if (!isNaN(lat) && !isNaN(lng)) {
      $("t-position").textContent = lat.toFixed(6) + ", " + lng.toFixed(6);
      window.DashMap.updateRover(
        lat,
        lng,
        parseFloat(data.orientation),
        DASH.mapZoomLevel
      );
    }

    updateCompass(parseFloat(data.orientation));
  }

  // Freshness ticker: age label + stale styling.
  setInterval(function () {
    var label = $("freshness");
    var panel = document.querySelector(".telemetry-panel");
    if (lastDataAt === null) {
      label.textContent = realtimeOn ? "waiting for data…" : "no data yet";
      return;
    }
    var age = (Date.now() - lastDataAt) / 1000;
    var stale = age > 5;
    panel.classList.toggle("stale", stale);
    if (!realtimeOn && !stale) {
      label.textContent = "paused";
    } else if (!realtimeOn) {
      label.textContent =
        "paused · last update " +
        new Date(lastDataAt).toLocaleTimeString();
    } else {
      label.textContent =
        age < 10 ? age.toFixed(1) + "s ago" : Math.round(age) + "s ago";
    }
  }, 1000);

  /* ---------- Real time WebSocket ---------- */

  var ws = null;
  var realtimeOn = false;
  var wsRetryTimer = null;

  function connectRealtime() {
    ws = new WebSocket(
      (location.protocol === "https:" ? "wss://" : "ws://") +
        location.host +
        "/ws/data?key=" +
        encodeURIComponent(DASH.apiKey || "")
    );
    ws.onopen = function () {
      setLed("led-telemetry", "on");
    };
    ws.onmessage = function (event) {
      var msg;
      try {
        msg = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      if (msg.type === "snapshot" || msg.type === "telemetry") {
        if (msg.data) renderTelemetry(msg.data);
      }
      if (msg.ingest_connected !== undefined) {
        setLed("led-rover", msg.ingest_connected ? "on" : "off");
      }
    };
    ws.onclose = function () {
      setLed("led-telemetry", realtimeOn ? "bad" : "off");
      if (realtimeOn) {
        wsRetryTimer = setTimeout(connectRealtime, 2000);
      }
    };
    ws.onerror = function () {
      ws.close();
    };
  }

  $("rt-switch").addEventListener("change", function () {
    realtimeOn = this.checked;
    if (realtimeOn) {
      connectRealtime();
    } else {
      clearTimeout(wsRetryTimer);
      if (ws) ws.close();
      ws = null;
      setLed("led-telemetry", "off");
    }
  });

  // One initial fetch so the page isn't blank while real time is off.
  apiFetch("/data")
    .then(function (r) {
      return r.ok ? r.json() : null;
    })
    .then(function (data) {
      if (data) renderTelemetry(data);
    })
    .catch(function () {});

  // Light status poll for the rover LED (works with real time off).
  function pollStatus() {
    apiFetch("/status")
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (status) {
        if (!status) return;
        var fresh =
          status.ingest_connected &&
          status.telemetry_age_s !== null &&
          status.telemetry_age_s < 5;
        setLed("led-rover", fresh ? "on" : "off");
      })
      .catch(function () {});
  }
  pollStatus();
  setInterval(pollStatus, 10000);

  /* ---------- Drive controls ---------- */

  var held = { forward: false, back: false, left: false, right: false };
  var driveTimer = null;
  var commandInFlight = false;
  var pendingCommand = null;
  var feedbackTimer = null;

  function speedScale() {
    return parseInt($("speed-slider").value, 10) / 100;
  }

  $("speed-slider").addEventListener("input", function () {
    $("speed-value").textContent = this.value + "%";
  });

  function feedback(text, isError) {
    var el = $("control-feedback");
    el.textContent = text;
    el.classList.toggle("err", !!isError);
    clearTimeout(feedbackTimer);
    if (text) {
      feedbackTimer = setTimeout(function () {
        el.textContent = "";
        el.classList.remove("err");
      }, 3000);
    }
  }

  function sendCommand(command) {
    if (!missionStarted) {
      // No ride, no rover: transmitting would only surface a 400/timeout.
      pendingCommand = null;
      return;
    }
    // Latest wins, but delivery remains ordered. In particular, a stop waits
    // behind an in-flight motion command instead of racing it over HTTP/RTM.
    pendingCommand = command;
    flushCommand();
  }

  function flushCommand() {
    if (commandInFlight || !pendingCommand) return;
    var command = pendingCommand;
    pendingCommand = null;
    commandInFlight = true;
    apiFetch("/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: command }),
      keepalive: true,
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (body) {
            throw new Error((body && body.detail) || "control failed");
          });
        }
      })
      .catch(function (err) {
        feedback(String(err.message || err), true);
      })
      .finally(function () {
        commandInFlight = false;
        flushCommand();
      });
  }

  function currentCommand() {
    var scale = speedScale();
    return {
      linear: ((held.forward ? 1 : 0) - (held.back ? 1 : 0)) * scale,
      angular: ((held.left ? 1 : 0) - (held.right ? 1 : 0)) * scale,
      lamp: lampState,
    };
  }

  function anyHeld() {
    return held.forward || held.back || held.left || held.right;
  }

  function refreshPads() {
    document.querySelectorAll(".pad[data-dir]").forEach(function (pad) {
      pad.classList.toggle("active", held[pad.dataset.dir]);
    });
  }

  function startDriving() {
    if (driveTimer) return;
    sendCommand(currentCommand());
    driveTimer = setInterval(function () {
      if (anyHeld()) sendCommand(currentCommand());
    }, 100); // 10 Hz while held
  }

  function stopDriving() {
    if (driveTimer) {
      clearInterval(driveTimer);
      driveTimer = null;
    }
    // Exactly one zero command so the rover stops promptly.
    sendCommand({ linear: 0, angular: 0, lamp: lampState });
  }

  // Halt the drive loop WITHOUT transmitting: for when the ride is already
  // over and the rover is gone — a stop command would only produce an error.
  function clearDriveState() {
    if (driveTimer) {
      clearInterval(driveTimer);
      driveTimer = null;
    }
    held.forward = held.back = held.left = held.right = false;
    pendingCommand = null;
    refreshPads();
  }

  function setHeld(dir, value) {
    if (held[dir] === value) return;
    held[dir] = value;
    refreshPads();
    if (anyHeld()) {
      startDriving();
    } else {
      stopDriving();
    }
  }

  function releaseAll() {
    held.forward = held.back = held.left = held.right = false;
    refreshPads();
    // Always transmit the stop. The emergency button and page-safety events
    // must not depend on potentially stale local key/pointer state.
    stopDriving();
  }

  // On-screen d-pad
  document.querySelectorAll(".pad[data-dir]").forEach(function (pad) {
    var dir = pad.dataset.dir;
    pad.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      pad.setPointerCapture(e.pointerId);
      setHeld(dir, true);
    });
    ["pointerup", "pointercancel"].forEach(function (evt) {
      pad.addEventListener(evt, function () {
        setHeld(dir, false);
      });
    });
  });

  $("stop-btn").addEventListener("click", releaseAll);

  // Keyboard: WASD + arrows
  var KEYMAP = {
    KeyW: "forward", ArrowUp: "forward",
    KeyS: "back", ArrowDown: "back",
    KeyA: "left", ArrowLeft: "left",
    KeyD: "right", ArrowRight: "right",
  };

  function keyTargetIsInput(e) {
    var tag = (e.target.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea";
  }

  document.addEventListener("keydown", function (e) {
    if (e.repeat || keyTargetIsInput(e)) return;
    var dir = KEYMAP[e.code];
    if (dir) {
      e.preventDefault();
      setHeld(dir, true);
    }
    if (e.code === "Space" && !keyTargetIsInput(e)) {
      e.preventDefault();
      releaseAll();
    }
  });

  document.addEventListener("keyup", function (e) {
    var dir = KEYMAP[e.code];
    if (dir) setHeld(dir, false);
  });

  // Safety: losing the page must stop the rover.
  window.addEventListener("blur", releaseAll);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) releaseAll();
  });

  // Lamp toggle: one command with current motion + flipped lamp bit.
  $("lamp-btn").addEventListener("click", function () {
    lampState = lampState ? 0 : 1;
    $("lamp-btn").textContent = lampState ? "Lamp off" : "Lamp on";
    $("lamp-btn").classList.toggle("on", !!lampState);
    var command = currentCommand();
    sendCommand(command);
  });

  /* ---------- Speak ---------- */

  $("speak-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var input = $("speak-input");
    var text = input.value.trim();
    if (!text) return;
    var btn = $("speak-btn");
    btn.disabled = true;
    btn.textContent = "Sending…";
    apiFetch("/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("speak failed (" + r.status + ")");
        input.value = "";
        feedback("speech sent to rover");
      })
      .catch(function (err) {
        feedback(String(err.message || err), true);
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "Speak";
      });
  });

  /* ---------- Missions panel (tabs) ---------- */

  var checkpoints = DASH.checkpointsList || [];
  var tabsLoaded = { available: false, history: false };

  document.querySelectorAll(".tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      var name = tab.dataset.tab;
      document.querySelectorAll(".tab").forEach(function (t) {
        t.classList.toggle("active", t === tab);
      });
      document.querySelectorAll(".tab-pane").forEach(function (pane) {
        pane.classList.add("hidden");
      });
      $("tab-" + name).classList.remove("hidden");
      $("checkpoint-btn").classList.toggle(
        "hidden",
        name !== "active" || checkpoints.length === 0
      );
      if (name === "available" && !tabsLoaded.available) loadAvailableMissions();
      if (name === "history" && !tabsLoaded.history) loadMissionsHistory();
    });
  });

  /* --- Active mission tab --- */

  if (checkpoints.length > 0) {
    $("active-mission-head").classList.remove("hidden");
    $("active-mission-slug").textContent = DASH.missionSlug || "mission";
    renderCheckpoints(null);

    apiFetch("/checkpoints-list")
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (body) {
        if (
          body &&
          body.latest_scanned_checkpoint !== null &&
          body.latest_scanned_checkpoint !== undefined
        ) {
          renderCheckpoints(parseInt(body.latest_scanned_checkpoint, 10) + 1);
        }
      })
      .catch(function () {});
  } else {
    $("active-empty").classList.remove("hidden");
    $("checkpoint-btn").classList.add("hidden");
  }

  function renderCheckpoints(nextSequence) {
    window.DashMap.setCheckpointStates(nextSequence);
    var list = $("checkpoint-chips");
    list.innerHTML = "";
    var done = 0;
    checkpoints.forEach(function (cp) {
      var li = document.createElement("li");
      li.textContent = "#" + cp.sequence;
      if (nextSequence !== null && nextSequence !== undefined) {
        if (cp.sequence < nextSequence) {
          li.classList.add("done");
          done += 1;
        }
        if (cp.sequence === nextSequence) li.classList.add("next");
      }
      list.appendChild(li);
    });
    $("active-mission-progress").textContent =
      done + " / " + checkpoints.length + " checkpoints";
  }

  /* --- Available missions tab --- */

  function loadAvailableMissions() {
    tabsLoaded.available = true;
    var list = $("available-list");
    list.innerHTML = '<p class="empty">Loading missions…</p>';
    apiFetch("/missions")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (body) {
        var missions = body.missions || [];
        list.innerHTML = "";
        if (missions.length === 0) {
          list.innerHTML =
            '<p class="empty">No missions available for this bot (personal bots have none).</p>';
          return;
        }
        missions.forEach(function (mission) {
          var row = document.createElement("div");
          row.className = "mission-row";
          addSpan(row, "mission-slug", mission.slug);
          addSpan(
            row,
            "mission-detail",
            formatDistance(mission.distance_in_m)
          );
          addSpan(row, "mission-detail", pluralize(mission.checkpoints_count));
          var spacer = document.createElement("span");
          spacer.className = "spacer";
          row.appendChild(spacer);
          if (mission.slug === DASH.missionSlug) {
            addBadge(row, "badge-live", "current");
          }
          list.appendChild(row);
        });
      })
      .catch(function () {
        tabsLoaded.available = false;
        list.innerHTML = '<p class="empty">Could not load missions.</p>';
      });
  }

  /* --- History tab --- */

  function loadMissionsHistory() {
    tabsLoaded.history = true;
    var list = $("history-list");
    list.innerHTML = '<p class="empty">Loading history…</p>';
    apiFetch("/missions-history")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (body) {
        var rides = body.mission_rides || [];
        list.innerHTML = "";
        if (rides.length === 0) {
          list.innerHTML = '<p class="empty">No missions ridden yet.</p>';
          return;
        }
        rides.forEach(function (ride) {
          var row = document.createElement("div");
          row.className = "mission-row";
          addSpan(row, "mission-slug", ride.mission_slug || "#" + ride.id);
          addSpan(row, "mission-detail", formatDate(ride.start_time));
          var scanned = ride.latest_scanned_checkpoint;
          addSpan(
            row,
            "mission-detail",
            pluralize(scanned === null || scanned === undefined ? 0 : scanned)
          );
          var spacer = document.createElement("span");
          spacer.className = "spacer";
          row.appendChild(spacer);
          if (ride.status === "active") {
            addBadge(row, "badge-live", "in progress");
          } else if (ride.success) {
            addBadge(row, "badge-ok", "success");
          } else {
            addBadge(row, "badge-bad", "failed");
          }
          list.appendChild(row);
        });
      })
      .catch(function () {
        tabsLoaded.history = false;
        list.innerHTML = '<p class="empty">Could not load missions history.</p>';
      });
  }

  /* --- shared row helpers --- */

  function addSpan(row, className, text) {
    var span = document.createElement("span");
    span.className = className;
    span.textContent = text;
    row.appendChild(span);
  }

  function addBadge(row, className, text) {
    var badge = document.createElement("span");
    badge.className = "badge " + className;
    badge.textContent = text;
    row.appendChild(badge);
  }

  function pluralize(count) {
    var n = parseInt(count, 10) || 0;
    return n + (n === 1 ? " checkpoint" : " checkpoints");
  }

  function formatDistance(meters) {
    var m = parseFloat(meters);
    if (isNaN(m)) return "";
    return m >= 1000 ? (m / 1000).toFixed(1) + " km" : Math.round(m) + " m";
  }

  function formatDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  // The backend ends the ride once the last checkpoint is scanned: the feed
  // drops and the bot stops listening. Reflect that instead of erroring.
  function missionCompleted() {
    missionStarted = false;
    clearDriveState();
    document
      .querySelectorAll(".pad, #lamp-btn, #speak-btn, #checkpoint-btn")
      .forEach(function (control) {
        control.disabled = true;
      });
    var maxSequence = checkpoints.reduce(function (acc, cp) {
      return Math.max(acc, cp.sequence);
    }, 0);
    renderCheckpoints(maxSequence + 1); // everything shows as done
    renderMissionAction();
    setPlaceholder("Mission completed 🎉", {
      sub:
        "All checkpoints scanned. The ride has ended and the rover " +
        "disconnected — start a new mission whenever you're ready.",
      startButton: !!DASH.missionSlug,
    });
    // The feed is dead either way; don't wait for an Agora unpublish event
    // that may never arrive to reveal the completion message.
    $("front-placeholder").classList.remove("hidden");
    $("rear-player").classList.add("hidden");
    $("ph-mission-btn").disabled = false;
    feedback("mission completed");
  }

  $("checkpoint-btn").addEventListener("click", function () {
    var btn = this;
    btn.disabled = true;
    apiFetch("/checkpoint-reached", { method: "POST" })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) {
            var detail = body && body.detail;
            var msg =
              detail && detail.proximate_distance_to_checkpoint
                ? "too far: " + detail.proximate_distance_to_checkpoint + "m"
                : "checkpoint not reached";
            throw new Error(msg);
          }
          return body;
        });
      })
      .then(function (body) {
        if (body.mission_completed) {
          missionCompleted();
        } else {
          feedback("checkpoint reached");
          if (body.next_checkpoint_sequence) {
            renderCheckpoints(parseInt(body.next_checkpoint_sequence, 10));
          }
        }
      })
      .catch(function (err) {
        feedback(String(err.message || err), true);
      })
      .finally(function () {
        // Stay disabled when the mission just completed.
        if (missionStarted) btn.disabled = false;
      });
  });

  /* ---------- Spectator video (Agora RTC) ---------- */

  var audioTrack = null;
  var audioTracks = {};
  var audioPlaying = false;

  function selectAudioTrack() {
    var nextTrack = audioTracks[1000] || audioTracks[1001] || null;
    if (nextTrack === audioTrack) return;

    var wasPlaying = audioPlaying;
    if (audioTrack && wasPlaying) audioTrack.stop();
    audioTrack = nextTrack;
    if (audioTrack && wasPlaying) audioTrack.play();
    if (!audioTrack) audioPlaying = false;
    $("audio-btn").disabled = !audioTrack;
    $("audio-btn").textContent = audioPlaying ? "Mute audio" : "Unmute audio";
  }

  function initVideo() {
    if (!DASH.appid || !DASH.rtcToken) {
      if (DASH.bootNotice && !/start-mission/i.test(DASH.bootNotice)) {
        // Auth/token failed (e.g. the bot is in use or its status is invalid).
        // Keep the real reason instead of overwriting it with a generic
        // "Video unavailable" message.
        reportProblem(DASH.bootNotice, true);
      } else if (missionStarted) {
        setPlaceholder("Bot unavailable for SDK", {
          tone: "warn",
          sub: "Couldn't get a video feed for this session — the bot may be offline or in use.",
        });
      }
      // Not started: the placeholder already shows the mission empty state.
      return;
    }

    var client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });

    client.on("connection-state-change", function (state) {
      setLed("led-video", state === "CONNECTED" ? "on" : "off");
    });

    var FRONT_UID = 1000;
    var REAR_UID = 1001;

    client.on("user-published", function (user, mediaType) {
      var uid = parseInt(user.uid, 10);
      // The rover publishes front on 1000 and rear on 1001; ignore anyone
      // else in the channel (other spectators, the headless /sdk page).
      if (uid !== FRONT_UID && uid !== REAR_UID) return;
      client
        .subscribe(user, mediaType)
        .then(function () {
          if (mediaType === "video") {
            if (uid === REAR_UID) {
              $("rear-player").classList.remove("hidden");
              user.videoTrack.play("rear-player");
            } else {
              $("front-placeholder").classList.add("hidden");
              user.videoTrack.play("front-player");
            }
          }
          if (mediaType === "audio") {
            // Rover models may publish audio with either camera UID. Prefer
            // the front publisher if both exist, and never call play() until
            // the user explicitly unmutes.
            audioTracks[uid] = user.audioTrack;
            selectAudioTrack();
          }
        })
        .catch(function (error) {
          console.error(
            "Failed to subscribe to rover " + mediaType + " (UID " + uid + ")",
            error
          );
        });
    });

    client.on("user-unpublished", function (user, mediaType) {
      var uid = parseInt(user.uid, 10);
      if (uid !== FRONT_UID && uid !== REAR_UID) return;
      if (mediaType === "video") {
        if (uid === REAR_UID) {
          $("rear-player").classList.add("hidden");
        } else {
          $("front-placeholder").classList.remove("hidden");
        }
      }
      if (mediaType === "audio") {
        delete audioTracks[uid];
        selectAudioTrack();
      }
    });

    client
      .join(
        DASH.appid,
        DASH.channel,
        DASH.rtcToken,
        parseInt(DASH.uid, 10) || null
      )
      .catch(function (err) {
        console.error("Agora join failed", err);
        setLed("led-video", "bad");
        setPlaceholder("Video connection failed", {
          tone: "error",
          sub: "Check the tokens and channel, then reload the page.",
        });
      });
  }

  $("audio-btn").addEventListener("click", function () {
    if (!audioTrack) return;
    if (audioPlaying) {
      audioTrack.stop();
      audioPlaying = false;
      this.textContent = "Unmute audio";
    } else {
      audioTrack.play();
      audioPlaying = true;
      this.textContent = "Mute audio";
    }
  });

  initVideo();
})();
