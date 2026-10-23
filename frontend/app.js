const DATA_URL = "../california_tourist_places_categorized.csv";
const STORAGE_SESSION = "daytrip.session.v1";
const STORAGE_DATA_PREFIX = "daytrip.userdata.v1:";
const CATEGORY_LABELS = {
  restaurant_dining: "Restaurant dining",
  cooking_experience: "Cooking experiences",
  live_music: "Live music",
  sightseeing: "Sightseeing",
  running: "Running",
  water_sports: "Water sports",
  walking: "Walking",
  nightlife: "Nightlife",
  hiking: "Hiking",
  photography: "Photography",
  zoo: "Zoo & wildlife",
  shopping: "Shopping",
  "art gallary/museum": "Art gallery / museum",
};
const CATEGORY_EMOJI = {
  restaurant_dining: "⌂", cooking_experience: "✳", live_music: "♫", sightseeing: "◉",
  running: "↗", water_sports: "≈", walking: "↝", nightlife: "☾", hiking: "△",
  photography: "▧", zoo: "♧", shopping: "◇", "art gallary/museum": "▤",
};

let places = [];
let categories = [];
let cities = [];
let currentUser = null;
let userData = { trips: [] };
let currentTrip = null;
let pendingInvite = null;
let toastTimeout;

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function parseCSV(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { field += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") { row.push(field); field = ""; }
    else if (char === "\n") { row.push(field.replace(/\r$/, "")); rows.push(row); row = []; field = ""; }
    else field += char;
  }
  if (field.length || row.length) { row.push(field.replace(/\r$/, "")); rows.push(row); }
  if (!rows.length) return [];
  const headers = rows.shift().map((value) => value.trim());
  return rows.filter((values) => values.some((value) => value !== "")).map((values) => {
    const record = {};
    headers.forEach((header, index) => { record[header] = values[index] ?? ""; });
    return record;
  });
}

function areaFromQuery(query = "") {
  const marker = query.lastIndexOf(" in ");
  const suffix = query.lastIndexOf(", California");
  if (marker >= 0 && suffix > marker) return query.slice(marker + 4, suffix).trim();
  return "";
}

function parseCategories(place) {
  return (place.category || "").split(",").map((value) => value.trim()).filter(Boolean);
}

async function loadPlaces() {
  const response = await fetch(DATA_URL);
  if (!response.ok) throw new Error(`Could not load place data (${response.status}).`);
  places = parseCSV(await response.text()).map((place) => ({
    ...place,
    id: place.place_id || `${place.name}-${place.address}`,
    categories: parseCategories(place),
    area: areaFromQuery(place.search_query),
  }));
  categories = [...new Set(places.flatMap((place) => place.categories))].sort((a, b) =>
    (CATEGORY_LABELS[a] || a).localeCompare(CATEGORY_LABELS[b] || b)
  );
  cities = [...new Set(places.map((place) => place.area).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  renderCities();
  renderInterests();
}

function renderCities() {
  const select = $("#trip-city");
  select.replaceChildren(new Option("Choose a city or destination", ""));
  cities.forEach((city) => select.add(new Option(city, city)));
}

function renderInterests() {
  const holder = $("#interest-options");
  holder.replaceChildren();
  $("#interest-empty").hidden = categories.length > 0;
  categories.forEach((category) => {
    const label = document.createElement("label");
    label.className = "interest-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "interest";
    checkbox.value = category;
    const text = document.createElement("span");
    text.textContent = `${CATEGORY_EMOJI[category] || "✳"}  ${CATEGORY_LABELS[category] || category}`;
    label.append(checkbox, text);
    holder.append(label);
  });
}

function userDataKey(email) { return `${STORAGE_DATA_PREFIX}${email.toLowerCase()}`; }

function readUserData(email) {
  try { return JSON.parse(localStorage.getItem(userDataKey(email)) || '{"trips":[]}'); }
  catch { return { trips: [] }; }
}

function saveUserData() {
  if (currentUser) localStorage.setItem(userDataKey(currentUser.email), JSON.stringify(userData));
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.remove("visible"), 2900);
}

function setAuthMode(mode) {
  const signup = mode === "signup";
  $("#signup-form").hidden = !signup;
  $("#login-form").hidden = signup;
  $("#signup-tab").classList.toggle("active", signup);
  $("#login-tab").classList.toggle("active", !signup);
  $("#signup-tab").setAttribute("aria-selected", String(signup));
  $("#login-tab").setAttribute("aria-selected", String(!signup));
}

function showPage(page) {
  const signedIn = Boolean(currentUser);
  document.querySelector("#kpd-planner-panel").hidden = !signedIn;
  if (page !== "auth" && !signedIn) page = "auth";
  $$(".page").forEach((section) => { section.hidden = section.id !== `page-${page}`; });
  $("#main-nav").hidden = !signedIn;
  $("#user-actions").hidden = !signedIn;
  $("#user-greeting").textContent = signedIn ? `Hi, ${currentUser.name.split(" ")[0]}` : "";
  $$(".nav-link").forEach((button) => button.classList.toggle("active", button.dataset.page === page));
  if (page === "recommendations") {
    renderRecommendations();
    if (currentTrip?.sharedTripId) refreshSharedRecommendations();
  }
  if (page === "saved") renderSaved();
  if (page === "plan") renderSharePanel();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function enterApp(user, startPage = "plan") {
  currentUser = { name: user.name, email: user.email.toLowerCase(), sessionToken: user.sessionToken };
  localStorage.setItem(STORAGE_SESSION, JSON.stringify(currentUser));
  userData = readUserData(currentUser.email);
  currentTrip = userData.trips[0] || null;
  showPage(startPage);
}

function signOut() {
  currentUser = null;
  currentTrip = null;
  userData = { trips: [] };
  localStorage.removeItem(STORAGE_SESSION);
  setAuthMode("login");
  showPage("auth");
}

function makeTripId() { return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`; }

function matchingPlaces(trip, includeSeen = false) {
  const interests = new Set(trip.interests);
  const rejected = new Set([...(trip.dislikedPlaceIds || []), ...(!includeSeen ? (trip.shownPlaceIds || []) : [])]);
  const inCity = places.filter((place) => place.area === trip.city || (place.address || "").toLowerCase().includes(trip.city.toLowerCase()));
  const cityMatches = inCity.filter((place) => place.categories.some((category) => interests.has(category)));
  const statewideMatches = places.filter((place) => place.categories.some((category) => interests.has(category)));
  const source = cityMatches.length ? cityMatches : statewideMatches;
  const ranked = source.map((place) => ({
    place,
    score: place.categories.reduce((score, category) => score + Number(interests.has(category)), 0),
  })).filter(({ place }) => !rejected.has(place.id));
  ranked.sort((a, b) => b.score - a.score || a.place.id.localeCompare(b.place.id));
  return { places: ranked.map((entry) => entry.place), usedFallback: cityMatches.length === 0 };
}

async function createTrip(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);

  const interests = form.getAll("interest");
  const city = form.get("city");
  const start = form.get("start");
  const end = form.get("end");

  if (!interests.length) { showToast("Choose at least one interest to get recommendations."); return; }
  if (end < start) { showToast("Your leaving date needs to be after your arrival date."); return; }
  
  // -----------------------------------------
    // Save onboarding activities to Neo4j
    // -----------------------------------------

    try {
        await postJSON(
            "/api/activities",
            {
                activities: interests
            }
        );
    } catch (error) {
        showToast(error.message);
        return;
    }
  
  currentTrip = {
    id: makeTripId(), city, start, end, interests,
    likedPlaceIds: [], dislikedPlaceIds: [], shownPlaceIds: [], visiblePlaceIds: [],
  };
  userData.trips.unshift(currentTrip);
  saveUserData();
  showPage("recommendations");
}

async function postJSON(url, payload) {
  const headers = { "Content-Type": "application/json" };
  if (currentUser?.sessionToken) headers.Authorization = `Bearer ${currentUser.sessionToken}`;
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The local server could not complete that request.");
  return result;
}

async function refreshSharedRecommendations() {
  if (!currentTrip?.sharedTripId) return;
  try {
    const response = await fetch('/api/shared-trips/' + encodeURIComponent(currentTrip.sharedTripId) + '/recommendations', {
      headers: { Authorization: 'Bearer ' + currentUser.sessionToken },
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not load group recommendations.');
    currentTrip.sharedRecommendations = result.recommendations;
    saveUserData();
    renderRecommendations();
  } catch (error) {
    showToast(error.message);
  }
}

function makeInviteURL(token) {
  const url = new URL(window.location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("invite", token);
  return url.toString();
}

function renderSharePanel() {
  const panel = $("#trip-share-panel");
  if (!panel) return;
  panel.hidden = !currentTrip;
  if (!currentTrip) return;
  const linkRow = $("#share-link-row");
  const button = $("#create-share-button");
  const count = $("#share-member-count");
  const linkInput = $("#share-link");
  if (currentTrip.sharedTripId) {
    const memberCount = currentTrip.memberCount || 1;
    linkInput.value = makeInviteURL(currentTrip.sharedTripId);
    linkRow.hidden = false;
    button.hidden = true;
    count.hidden = false;
    count.textContent = `${memberCount} ${memberCount === 1 ? "traveler" : "travelers"} on this trip`;
  } else {
    linkRow.hidden = true;
    button.hidden = false;
    button.disabled = false;
    button.textContent = "Create invite link";
    count.hidden = true;
  }
}

async function createInviteLink() {
  if (!currentTrip || !currentUser) return;
  const button = $("#create-share-button");
  button.disabled = true;
  button.textContent = "Creating link…";
  try {
    const result = await postJSON("/api/shared-trips", {
      email: currentUser.email,
      city: currentTrip.city,
      start: currentTrip.start,
      end: currentTrip.end,
      interests: currentTrip.interests,
      candidateIds: matchingPlaces(currentTrip, true).places.slice(0, 500).map((place) => place.id),
    });
    currentTrip.sharedTripId = result.trip.token;
    currentTrip.sharedOwnerName = result.trip.ownerName;
    currentTrip.memberCount = result.trip.memberCount;
    saveUserData();
    renderSharePanel();
    await refreshSharedRecommendations();
    showToast("Your trip invite link is ready to copy.");
  } catch (error) {
    button.disabled = false;
    button.textContent = "Create invite link";
    showToast(error.message);
  }
}

async function copyInviteLink() {
  const input = $("#share-link");
  try {
    await navigator.clipboard.writeText(input.value);
  } catch {
    input.focus();
    input.select();
    document.execCommand("copy");
  }
  showToast("Invite link copied.");
}

async function loadInvite() {
  const token = new URLSearchParams(window.location.search).get("invite");
  if (!token) return;
  try {
    const response = await fetch(`/api/shared-trips/${encodeURIComponent(token)}`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "This invite link is unavailable.");
    pendingInvite = result.trip;
    const banner = $("#invite-banner");
    banner.textContent = `${pendingInvite.ownerName} invited you to join a trip to ${pendingInvite.city}, ${formatDate(pendingInvite.start)} – ${formatDate(pendingInvite.end)}. Create an account or log in to join with the same trip details.`;
    banner.hidden = false;
    setAuthMode("signup");
  } catch (error) {
    showToast(error.message);
  }
}

async function finishAuthentication(user, startPage = "plan") {
  enterApp(user, startPage);
  if (!pendingInvite) return;
  try {
    const result = await postJSON("/api/shared-trips/join", {
      token: pendingInvite.token,
      email: currentUser.email,
    });
    const shared = result.trip;
    let trip = userData.trips.find((item) => item.sharedTripId === shared.token);
    if (!trip) {
      trip = {
        id: `shared-${shared.token}`,
        sharedTripId: shared.token,
        city: shared.city,
        start: shared.start,
        end: shared.end,
        interests: shared.interests,
        likedPlaceIds: [],
        dislikedPlaceIds: [],
        shownPlaceIds: [],
        visiblePlaceIds: [],
      };
      userData.trips.unshift(trip);
    }
    trip.city = shared.city;
    trip.start = shared.start;
    trip.end = shared.end;
    trip.interests = shared.interests;
    trip.sharedTripId = shared.token;
    trip.sharedOwnerName = shared.ownerName;
    trip.memberCount = shared.memberCount;
    currentTrip = trip;
    saveUserData();
    pendingInvite = null;
    $("#invite-banner").hidden = true;
    window.history.replaceState({}, "", window.location.pathname);
    showPage("recommendations");
    showToast(`You joined ${shared.ownerName}’s ${shared.city} trip.`);
  } catch (error) {
    showToast(`Your account is ready, but the trip invite could not be joined: ${error.message}`);
  }
}

function placeCard(place, index, liked = false) {
  const article = document.createElement("article");
  article.className = "place-card";
  const top = document.createElement("div");
  top.className = "place-card-top";
  const number = document.createElement("span");
  number.className = "place-index";
  number.textContent = String(index).padStart(2, "0");
  const glyph = document.createElement("span");
  glyph.className = "place-glyph";
  const category = place.categories[0];
  glyph.textContent = CATEGORY_EMOJI[category] || "✳";
  top.append(number, glyph);

  const body = document.createElement("div");
  body.className = "place-card-body";
  const title = document.createElement("h2");
  title.textContent = place.name || "California place to explore";
  const address = document.createElement("p");
  address.className = "place-address";
  address.textContent = place.address || place.area || "California";
  const tags = document.createElement("div");
  tags.className = "place-tags";
  place.categories.slice(0, 3).forEach((value) => {
    const tag = document.createElement("span");
    tag.className = "place-tag";
    tag.textContent = CATEGORY_LABELS[value] || value;
    tags.append(tag);
  });
  const actions = document.createElement("div");
  actions.className = "place-actions";
  const buttons = document.createElement("div");
  buttons.className = "card-button-group";
  if (!liked) {
    const like = document.createElement("button");
    like.className = "button button-outline save-button";
    like.type = "button";
    like.textContent = "♡ Like";
    like.addEventListener("click", () => likePlace(place.id));
    const dislike = document.createElement("button");
    dislike.className = "button button-outline";
    dislike.type = "button";
    dislike.textContent = "Skip";
    dislike.addEventListener("click", () => dislikePlace(place.id));
    buttons.append(like, dislike);
  } else {
    const saved = document.createElement("span");
    saved.className = "saved-indicator";
    saved.textContent = "♥ Saved";
    buttons.append(saved);
  }
  const mapLink = document.createElement("a");
  mapLink.className = "map-link";
  mapLink.href = place.google_maps_url || `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(place.name || place.address || "California")}`;
  mapLink.target = "_blank";
  mapLink.rel = "noopener noreferrer";
  mapLink.textContent = "Map ↗";
  actions.append(buttons, mapLink);
  body.append(title, address, tags, actions);
  article.append(top, body);
  return article;
}

function renderRecommendations() {
  const grid = $("#recommendation-grid");
  grid.replaceChildren();
  const empty = $("#recommendation-empty");
  const notice = $("#recommendation-notice");
  if (!currentTrip) {
    empty.hidden = false;
    $("#recommendation-summary").textContent = "Start with a city, your dates, and a few interests.";
    notice.hidden = true;
    return;
  }
  const { places: candidates, usedFallback } = matchingPlaces(currentTrip, true);
  const liked = new Set(currentTrip.likedPlaceIds || []);
  if (currentTrip.sharedTripId && currentTrip.sharedRecommendations) {
    const shared = currentTrip.sharedRecommendations;
    const pool = (shared.candidateIds || []).map((id) => places.find((place) => place.id === id)).filter(Boolean);
    const votes = shared.votes || {};
    const unrated = pool.filter((place) => !(place.id in votes));
    let visible;
    if (!unrated.length) {
      visible = [...pool].sort((a, b) =>
        (shared.likeCounts?.[b.id] || 0) - (shared.likeCounts?.[a.id] || 0) || pool.indexOf(a) - pool.indexOf(b)
      ).slice(0, 5);
      $("#recommendation-summary").textContent = currentTrip.city + ' · Group favorites · ' + formatDate(currentTrip.start) + ' – ' + formatDate(currentTrip.end);
      notice.hidden = false;
      notice.textContent = 'These are the five places with the most likes from your group. Group likes: ' + visible.map((place) => place.name + ' (' + (shared.likeCounts?.[place.id] || 0) + ')').join(' · ');
      empty.hidden = visible.length > 0;
      visible.forEach((place, index) => grid.append(placeCard(place, index + 1, liked.has(place.id))));
      return;
    }
    visible = unrated.slice(0, 5);
    $("#recommendation-summary").textContent = currentTrip.city + ' · ' + formatDate(currentTrip.start) + ' – ' + formatDate(currentTrip.end);
    notice.hidden = false;
    notice.textContent = unrated.length + ' places left for you to rate. Your likes help choose the group’s final five.';
    empty.hidden = visible.length > 0;
    visible.forEach((place, index) => grid.append(placeCard(place, index + 1, false)));
    return;
  }
  let visible = (currentTrip.visiblePlaceIds || []).map((id) => places.find((place) => place.id === id)).filter(Boolean);
  if (!visible.length) {
    visible = candidates.filter((place) => !liked.has(place.id)).slice(0, 5);
    currentTrip.visiblePlaceIds = visible.map((place) => place.id);
    currentTrip.shownPlaceIds = [...new Set([...(currentTrip.shownPlaceIds || []), ...currentTrip.visiblePlaceIds])];
    saveUserData();
  }
  $("#recommendation-summary").textContent = `${currentTrip.city} · ${formatDate(currentTrip.start)} – ${formatDate(currentTrip.end)}`;
  notice.hidden = !usedFallback;
  notice.textContent = usedFallback ? `We didn’t find places for those interests in ${currentTrip.city}, so we’re showing matching places from elsewhere in California.` : "";
  empty.hidden = visible.length > 0;
  visible.forEach((place, index) => grid.append(placeCard(place, index + 1, liked.has(place.id))));
}

async function submitSharedVote(placeId, liked) {
  try {
    const result = await postJSON('/api/shared-trips/vote', {
      token: currentTrip.sharedTripId,
      placeId,
      liked,
    });
    currentTrip.sharedRecommendations = result.recommendations;
    if (liked && !currentTrip.likedPlaceIds.includes(placeId)) currentTrip.likedPlaceIds.push(placeId);
    saveUserData();
    renderRecommendations();
  } catch (error) {
    showToast(error.message);
  }
}

async function likePlace(placeId) {
  if (!currentTrip) return;

  if (currentTrip.sharedTripId) {
    submitSharedVote(placeId, true);
    return;
  }

  try {
    const result = await postJSON("/api/interest", {
      placeId: placeId,
      feedback: "like"
    });

    if (!currentTrip.likedPlaceIds.includes(placeId)) {
      currentTrip.likedPlaceIds.push(placeId);
    }

    currentTrip.dislikedPlaceIds =
      (currentTrip.dislikedPlaceIds || [])
        .filter(id => id !== placeId);

    saveUserData();

    console.log("LIKE RESULT:", result);

    showToast("Saved to your trip.");

    renderRecommendations();

  } catch (error) {
    console.error(error);
    showToast(error.message);
  }
}


async function dislikePlace(placeId) {
  if (!currentTrip) return;

  if (currentTrip.sharedTripId) {
    submitSharedVote(placeId, false);
    return;
  }

  try {
    const result = await postJSON("/api/interest", {
      placeId: placeId,
      feedback: "dislike"
    });

    if (!currentTrip.dislikedPlaceIds.includes(placeId)) {
      currentTrip.dislikedPlaceIds.push(placeId);
    }

    currentTrip.likedPlaceIds =
      (currentTrip.likedPlaceIds || [])
        .filter(id => id !== placeId);

    currentTrip.visiblePlaceIds =
      (currentTrip.visiblePlaceIds || [])
        .filter(id => id !== placeId);

    currentTrip.shownPlaceIds = [
      ...new Set([
        ...(currentTrip.shownPlaceIds || []),
        placeId
      ])
    ];

    const replacement =
      matchingPlaces(currentTrip).places[0];

    if (replacement) {
      currentTrip.visiblePlaceIds.push(
        replacement.id
      );

      currentTrip.shownPlaceIds = [
        ...new Set([
          ...currentTrip.shownPlaceIds,
          replacement.id
        ])
      ];
    }

    saveUserData();

    console.log("DISLIKE RESULT:", result);

    renderRecommendations();

  } catch (error) {
    console.error(error);
    showToast(error.message);
  }
}

function formatDate(value) {
  if (!value) return "Date not set";
  return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function renderSaved() {
  const container = $("#saved-content");
  container.replaceChildren();
  if (!userData.trips.length) {
    const empty = document.createElement("div");
    empty.className = "no-saved";
    empty.textContent = "No trips yet. Plan one and like a few places to start your collection.";
    container.append(empty);
    return;
  }
  userData.trips.forEach((trip) => {
    const section = document.createElement("section");
    section.className = "saved-trip";
    const header = document.createElement("div");
    header.className = "saved-trip-header";
    const info = document.createElement("div");
    const title = document.createElement("h2");
    title.textContent = trip.city;
    const dates = document.createElement("p");
    dates.textContent = `${formatDate(trip.start)} – ${formatDate(trip.end)}`;
    info.append(title, dates);
    const reopen = document.createElement("button");
    reopen.className = "button button-outline";
    reopen.type = "button";
    reopen.textContent = "Open recommendations";
    reopen.addEventListener("click", () => { currentTrip = trip; showPage("recommendations"); });
    header.append(info, reopen);
    section.append(header);

    if (trip.sharedTripId) {
      const sharedStatus = document.createElement("p");
      sharedStatus.className = "shared-status";
      sharedStatus.textContent = `Shared trip with ${trip.sharedOwnerName || currentUser.name} · ${trip.memberCount || 1} ${(trip.memberCount || 1) === 1 ? "traveler" : "travelers"}`;
      section.append(sharedStatus);
    }

    const savedPlaces = (trip.likedPlaceIds || []).map((id) => places.find((place) => place.id === id)).filter(Boolean);
    if (savedPlaces.length) {
      const list = document.createElement("div");
      list.className = "saved-place-list";
      savedPlaces.forEach((place) => {
        const link = document.createElement("a");
        link.className = "saved-place-chip";
        link.href = place.google_maps_url || `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(place.name || "California")}`;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        const heart = document.createElement("span");
        heart.textContent = "♥";
        link.append(heart, document.createTextNode(place.name || "Saved place"));
        list.append(link);
      });
      section.append(list);
    } else {
      const note = document.createElement("p");
      note.className = "fine-print";
      note.textContent = "No saved places yet — open your recommendations and like the ones you love.";
      section.append(note);
    }
    container.append(section);
  });
}

function bindEvents() {
  $("#recommendation-chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#chat-input");
    const message = input.value.trim();
    if (!message) return;
    const messages = $("#chat-messages");
    $(".chat-placeholder", messages)?.remove();
    const userMessage = document.createElement("p");
    userMessage.className = "chat-message user";
    userMessage.textContent = message;
    const assistantMessage = document.createElement("p");
    assistantMessage.className = "chat-message assistant";
    assistantMessage.textContent = "Thanks — your message is ready for your chatbot model. Recommendations haven’t been changed.";
    messages.append(userMessage, assistantMessage);
    messages.scrollTop = messages.scrollHeight;
    input.value = "";
    input.focus();
  });
  $("#signup-tab").addEventListener("click", () => setAuthMode("signup"));
  $("#login-tab").addEventListener("click", () => setAuthMode("login"));
  $("#signup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name")).trim();
    const email = String(form.get("email")).trim().toLowerCase();
    const password = String(form.get("password"));
    try {
      const response = await fetch("/api/signup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email, password }),
      });
      const result = await response.json();
      if (!response.ok) { showToast(result.error || "Could not create the account."); return; }
      await finishAuthentication({ ...result.user, sessionToken: result.sessionToken });
    } catch {
      showToast("Could not reach the local account server. Start it with python3 frontend/server.py.");
    }
  });
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email")).trim().toLowerCase();
    const password = String(form.get("password"));
    try {
      const response = await fetch("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const result = await response.json();
      if (!response.ok) { showToast(result.error || "Could not log in."); return; }
      await finishAuthentication({ ...result.user, sessionToken: result.sessionToken }, readUserData(email).trips.length ? "saved" : "plan");
    } catch {
      showToast("Could not reach the local account server. Start it with python3 frontend/server.py.");
    }
  });
  $("#trip-form").addEventListener("submit", createTrip);
  $("#create-share-button").addEventListener("click", createInviteLink);
  $("#copy-share-link").addEventListener("click", copyInviteLink);
  $("#logout-button").addEventListener("click", signOut);
  $$("[data-page]").forEach((button) => button.addEventListener("click", () => showPage(button.dataset.page)));
  const today = new Date();
  const dateValue = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  $("#trip-start").min = dateValue;
  $("#trip-end").min = dateValue;
  $("#trip-start").addEventListener("change", (event) => { $("#trip-end").min = event.target.value || dateValue; });
}

async function init() {
  bindEvents();
  try {
    await loadPlaces();
  } catch (error) {
    showToast("Couldn’t load the places CSV. Start the site from the project folder using a local web server.");
    console.error(error);
  }
  await loadInvite();
  window.setInterval(() => {
    const recommendationPage = $("#page-recommendations");
    if (document.visibilityState === "visible" && recommendationPage && !recommendationPage.hidden && currentTrip?.sharedTripId) {
      refreshSharedRecommendations();
    }
  }, 10000);
  try {
    const savedUser = JSON.parse(localStorage.getItem(STORAGE_SESSION) || "null");
    if (savedUser && savedUser.email && savedUser.sessionToken) {
      const response = await fetch("/api/session", {
        headers: { Authorization: `Bearer ${savedUser.sessionToken}` },
      });
      if (response.ok) {
        const result = await response.json();
        await finishAuthentication({ ...result.user, sessionToken: savedUser.sessionToken }, readUserData(savedUser.email).trips.length ? "saved" : "plan");
        return;
      }
      localStorage.removeItem(STORAGE_SESSION);
    }
  } catch { /* Use the signed-out screen when browser storage is unavailable. */ }
  showPage("auth");
}

init();


async function generateKpdItinerary() {
  const prompt = document.querySelector('#kpd-trip-prompt').value.trim();
  const status = document.querySelector('#kpd-status');
  const result = document.querySelector('#kpd-result');
  if (!prompt) { status.textContent = 'Describe your trip first.'; return; }
  status.textContent = 'Planning your itinerary…';
  result.replaceChildren();
  try {
    const body = await postJSON('/api/itineraries', {
      prompt, sharedTripId: currentTrip?.sharedTripId || null
    });
    const response = await pollKpdJob(body.job_id);
    if (response.status !== 'completed') throw new Error(response.error || 'Planner did not finish.');
    status.textContent = 'Your itinerary is ready.';
    const slots = Array.isArray(response.slots) ? response.slots :
      (Array.isArray(response.plan) ? response.plan : []);
    for (const day of slots) {
      const section = document.createElement('section');
      section.className = 'kpd-day';
      const heading = document.createElement('h3');
      heading.textContent = `${day.date || ''} · ${day.city || ''}`;
      section.append(heading);
      for (const item of (day.activities || day.stops || [])) {
        const p = document.createElement('p');
        const time = item.start || item.start_time ||
          (Number.isFinite(item.start_min) ? `${Math.floor(item.start_min/60)}:${String(item.start_min%60).padStart(2,'0')}` : '');
        p.textContent = `${time}  ${item.name || item.place_name || 'Experience'}`;
        section.append(p);
      }
      result.append(section);
    }
    if (response.itinerary) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = 'Read the full itinerary';
      const pre = document.createElement('pre');
      pre.textContent = response.itinerary;
      details.append(summary, pre);
      result.append(details);
    }
  } catch (error) { status.textContent = error.message; }
}

async function pollKpdJob(jobId) {
  for (let i = 0; i < 240; i++) {
    await new Promise(resolve => setTimeout(resolve, 4000));
    const response = await fetch('/api/itineraries/' + encodeURIComponent(jobId), {
      headers: { Authorization: 'Bearer ' + currentUser.sessionToken }
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Could not read itinerary job.');
    if (body.status !== 'running') return body;
  }
  throw new Error('The planner is still running; please try again later.');
}

document.querySelector('#kpd-generate').addEventListener('click', generateKpdItinerary);
