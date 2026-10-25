const DATA_URL = "../data/california_tourist_places_categorized.csv";
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
let planningNewTrip = false;
let toastTimeout;
let activeItinerarySlots = [];
let activeItineraryText = "";
let displayedItineraryTripKey = null;

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

function itineraryPlaceFor(item) {
  const placeId = item.place_id || item.placeId || item.id;
  const name = item.name || item.place_name || "";
  return places.find((place) => place.id === placeId) ||
    places.find((place) => name && place.name.trim().toLocaleLowerCase() === name.trim().toLocaleLowerCase());
}

function itineraryPlaceDescription(place, item, city) {
  const explicit = place?.description || item.description || item.summary;
  if (explicit) return explicit;
  const tags = place?.categories?.length ? place.categories : (item.themes || [item.experience, item.category]).filter(Boolean);
  const labels = tags.map((tag) => CATEGORY_LABELS[tag] || tag.replaceAll("_", " ")).slice(0, 2);
  const area = place?.area || city || "California";
  return labels.length
    ? `A ${labels.join(" and ").toLowerCase()} experience in ${area}.`
    : `A place to explore in ${area}.`;
}

function itineraryStopList() {
  return activeItinerarySlots.flatMap((day) => day.activities || day.stops || []);
}

async function replaceItineraryStop(target, day) {
  const currentPlace = itineraryPlaceFor(target);
  const targetCategories = new Set(currentPlace?.categories?.length ? currentPlace.categories : (target.themes || [target.experience, target.category]).filter(Boolean));
  const otherStops = itineraryStopList().filter((stop) => stop !== target);
  const usedIds = new Set(otherStops.map((stop) => {
    const place = itineraryPlaceFor(stop);
    return place?.id || stop.place_id || stop.placeId || stop.name || stop.place_name;
  }));
  const usedNames = new Set(otherStops.map((stop) => (stop.name || stop.place_name || "").trim().toLocaleLowerCase()));
  const oldName = target.name || target.place_name || "";
  const candidates = places.filter((place) => place.name !== oldName && !usedIds.has(place.id) && !usedNames.has(place.name.trim().toLocaleLowerCase()));
  const currentArea = currentPlace?.area || day.city || currentTrip?.city || "";
  const score = (place) => {
    const candidateCategories = new Set(place.categories);
    const overlap = [...targetCategories].filter((category) => candidateCategories.has(category)).length;
    const union = new Set([...targetCategories, ...candidateCategories]).size;
    const categorySimilarity = union ? overlap / union : 0;
    const sameArea = currentArea && place.area && place.area.toLowerCase() === currentArea.toLowerCase();
    const sameDestination = currentTrip?.city && place.area && place.area.toLowerCase() === currentTrip.city.toLowerCase();
    return categorySimilarity * 100 + overlap * 10 + Number(Boolean(sameArea)) * 8 + Number(Boolean(sameDestination)) * 4;
  };
  const replacement = candidates.sort((a, b) => score(b) - score(a) || a.name.localeCompare(b.name))[0];
  if (!replacement) {
    showToast("There are no unused places available to replace this stop.");
    return;
  }
  if (currentTrip?.sharedTripId) {
    try {
      await postJSON("/api/shared-trips/itinerary-vote", {
        token: currentTrip.sharedTripId,
        placeId: currentPlace?.id || target.place_id || target.name,
        placeName: oldName,
        choice: "replace",
        replacementPlaceId: replacement.id,
        replacementName: replacement.name,
      });
    } catch (error) {
      showToast(error.message);
      return;
    }
  }
  target.name = replacement.name;
  target.place_name = replacement.name;
  target.place_id = replacement.id;
  target.address = replacement.address;
  target.maps_url = replacement.google_maps_url;
  target.themes = replacement.categories;
  target.experience = replacement.categories[0] || target.experience;
  target.category = replacement.categories[0] || target.category;
  activeItineraryText = "";
  renderKpdItinerary();
  $("#kpd-status").textContent = `Replaced ${oldName || "the stop"} with ${replacement.name}.`;
  if (currentTrip?.sharedTripId) {
    refreshSharedTripState(false).then(() => renderKpdItinerary()).catch(() => {});
  }
}

async function voteForItineraryPlace(item, choice) {
  if (!currentTrip?.sharedTripId) return;
  const place = itineraryPlaceFor(item);
  const placeId = place?.id || item.place_id || item.placeId || item.name || item.place_name;
  try {
    await postJSON("/api/shared-trips/itinerary-vote", {
      token: currentTrip.sharedTripId,
      placeId,
      placeName: item.name || item.place_name || place?.name || placeId,
      choice,
    });
    await refreshSharedTripState(false);
    $("#kpd-status").textContent = choice === "like" ? "Your like was added to the group vote." : "Your replacement vote was added to the group vote.";
    renderKpdItinerary();
  } catch (error) {
    showToast(error.message);
  }
}

function renderKpdItinerary() {
  const result = $("#kpd-result");
  result.replaceChildren();
  for (const day of activeItinerarySlots) {
    const section = document.createElement('section');
    section.className = 'kpd-day';
    const heading = document.createElement('h3');
    heading.textContent = `${day.date || ''} · ${day.city || ''}`;
    section.append(heading);
    const timetable = document.createElement('div');
    timetable.className = 'kpd-timetable';
    for (const item of (day.activities || day.stops || [])) {
      const place = itineraryPlaceFor(item);
      const row = document.createElement('article');
      row.className = 'kpd-stop';
      const time = document.createElement('time');
      time.className = 'kpd-stop-time';
      const rawTime = item.start || item.start_time ||
        (Number.isFinite(item.start_min) ? `${String(Math.floor(item.start_min / 60)).padStart(2, '0')}:${String(item.start_min % 60).padStart(2, '0')}` : '');
      time.textContent = rawTime || 'Time TBD';
      if (rawTime) time.setAttribute('datetime', rawTime);

      const details = document.createElement('div');
      details.className = 'kpd-stop-details';
      const titleRow = document.createElement('div');
      titleRow.className = 'kpd-stop-title-row';
      const title = document.createElement('h4');
      title.textContent = item.name || item.place_name || place?.name || 'Experience';
      const replace = document.createElement('button');
      replace.className = 'button button-outline kpd-replace-button';
      replace.type = 'button';
      replace.textContent = 'Replace';
      replace.addEventListener('click', () => replaceItineraryStop(item, day));
      titleRow.append(title, replace);
      let voteActions = null;
      if (currentTrip?.sharedTripId) {
        voteActions = document.createElement("div");
        voteActions.className = "kpd-vote-actions";
        const like = document.createElement("button");
        like.className = "button button-outline kpd-replace-button";
        like.type = "button";
        const voteKey = place?.id || item.place_id || item.placeId || item.name || item.place_name;
        like.textContent = currentTrip.sharedVotes?.mine?.[voteKey]?.choice === "like" ? "Liked" : "Like";
        like.addEventListener("click", () => voteForItineraryPlace(item, "like"));
        voteActions.append(like);
      }
      const description = document.createElement('p');
      description.className = 'kpd-stop-description';
      description.textContent = itineraryPlaceDescription(place, item, day.city);
      const categoryValues = place?.categories?.length ? place.categories : (item.themes || [item.experience, item.category]).filter(Boolean);
      const tags = document.createElement('div');
      tags.className = 'kpd-stop-categories';
      [...new Set(categoryValues)].forEach((category) => {
        const tag = document.createElement('span');
        tag.className = 'place-tag';
        tag.textContent = CATEGORY_LABELS[category] || category.replaceAll('_', ' ');
        tags.append(tag);
      });
      details.append(titleRow, description, tags);
      if (voteActions) {
        const voteKey = place?.id || item.place_id || item.placeId || item.name || item.place_name;
        const totals = currentTrip.sharedVotes?.counts?.[voteKey] || { likes: 0, replacements: 0 };
        const tally = document.createElement("span");
        tally.className = "kpd-vote-tally fine-print";
        tally.textContent = `${totals.likes} like${totals.likes === 1 ? "" : "s"} · ${totals.replacements} replace vote${totals.replacements === 1 ? "" : "s"}`;
        voteActions.append(tally);
        details.append(voteActions);
      }

      const location = document.createElement('div');
      location.className = 'kpd-stop-location';
      const address = document.createElement('p');
      address.textContent = item.address || place?.address || day.city || 'California';
      const mapLink = document.createElement('a');
      mapLink.className = 'map-link';
      mapLink.href = item.maps_url || item.map_url || place?.google_maps_url || `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(title.textContent + (address.textContent ? ` ${address.textContent}` : ''))}`;
      mapLink.target = '_blank';
      mapLink.rel = 'noopener noreferrer';
      mapLink.textContent = 'Map ↗';
      location.append(address, mapLink);
      row.append(time, details, location);
      timetable.append(row);
    }
    section.append(timetable);
    result.append(section);
  }
  if (activeItineraryText) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Read the full itinerary';
    const pre = document.createElement('pre');
    pre.textContent = activeItineraryText;
    details.append(summary, pre);
    result.append(details);
  }
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
  categories = [...new Set([...Object.keys(CATEGORY_LABELS), ...places.flatMap((place) => place.categories)])].sort((a, b) =>
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
  const holder = $("#signup-interest-options");
  if (!holder) return;
  holder.replaceChildren();
  if (!categories.length) {
    const message = document.createElement("p");
    message.className = "empty-note";
    message.textContent = "No categories were found in the places data.";
    holder.append(message);
  }
  categories.forEach((category) => {
    const label = document.createElement("label");
    label.className = "interest-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "interest";
    checkbox.value = category;
    checkbox.addEventListener("change", () => {
      label.classList.toggle("is-selected", checkbox.checked);
      updateInterestSelectionStatus();
    });
    const text = document.createElement("span");
    text.textContent = `${CATEGORY_EMOJI[category] || "✳"}  ${CATEGORY_LABELS[category] || category}`;
    label.append(checkbox, text);
    holder.append(label);
  });
  updateInterestSelectionStatus();
}

function updateInterestSelectionStatus() {
  const holder = $("#signup-interest-options");
  const status = $("#interest-selection-status");
  if (!holder || !status) return;
  const count = holder.querySelectorAll('input[name="interest"]:checked').length;
  status.textContent = count ? `${count} ${count === 1 ? "interest" : "interests"} selected` : "No interests selected";
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
  if (page !== "auth" && !signedIn) page = "auth";
  if (page === "plan") planningNewTrip = true;
  $$(".page").forEach((section) => { section.hidden = section.id !== `page-${page}`; });
  $("#main-nav").hidden = !signedIn;
  $("#user-actions").hidden = !signedIn;
  $("#user-greeting").textContent = signedIn ? `Hi, ${currentUser.name.split(" ")[0]}` : "";
  $$(".nav-link").forEach((button) => button.classList.toggle("active", button.dataset.page === page));
  if (page === "recommendations") {
    openRecommendationsPage();
  }
  if (page === "saved") renderSaved();
  if (page === "plan") renderGroupTripControls();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function buildInitialItineraryPrompt(voteDirectives = "") {
  if (!currentTrip) return "";
  const travelingWithOthers = Boolean(currentTrip.sharedTripId);
  const members = currentTrip.groupMembers || [];
  const weatherRequest = `Please account for the weather in ${currentTrip.city} from ${formatDate(currentTrip.start)} to ${formatDate(currentTrip.end)} and suggest suitable alternatives for outdoor activities if conditions are poor.`;
  if (travelingWithOthers) {
    const memberInterests = members.length
      ? members.flatMap((member) => member.interests || [])
      : (currentUser?.interests || currentTrip.interests || []);
    const allInterests = [...new Set(memberInterests.map((interest) => CATEGORY_LABELS[interest] || interest))];
    const interestText = allInterests.length ? allInterests.join(", ") : "no activities listed";
    const placesToInclude = voteDirectives.match(/Places to include:\s*(.+?)(?:\.|$)/i)?.[1]?.trim();
    const placesNotToInclude = voteDirectives.match(/Places not to include:\s*(.+?)(?:\.|$)/i)?.[1]?.trim();
    const placeRequests = [
      placesToInclude ? `Places to include: ${placesToInclude}.` : "",
      placesNotToInclude ? `Places not to include: ${placesNotToInclude}.` : "",
    ].filter(Boolean);
    const placeRequestText = placeRequests.length ? `\n${placeRequests.join("\n")}` : "";
    return `Plan a group trip to ${currentTrip.city} from ${formatDate(currentTrip.start)} to ${formatDate(currentTrip.end)}\nI am traveling with a group of ${currentTrip.memberCount || members.length || 1} travelers and we are interested in these activities:\n${interestText}\nPlease account for the weather during these dates and suggest suitable alternatives for outdoor activities if conditions are poor.${placeRequestText}`;
  }
  const prompt = [
    `Plan a trip to ${currentTrip.city} from ${formatDate(currentTrip.start)} to ${formatDate(currentTrip.end)}.`,
    `My interests are: ${(currentUser?.interests || currentTrip.interests || []).map((interest) => CATEGORY_LABELS[interest] || interest).join(", ") || "a well-rounded mix of local experiences"}.`,
    weatherRequest,
  ];
  if (voteDirectives) prompt.push(voteDirectives);
  return prompt.join("\n");
}

async function openRecommendationsPage() {
  if (!currentTrip) return;
  const tripKey = currentTrip.sharedTripId || currentTrip.id;
  if (tripKey !== displayedItineraryTripKey) {
    displayedItineraryTripKey = tripKey;
    activeItinerarySlots = [];
    activeItineraryText = "";
    $("#kpd-result").replaceChildren();
  }
  if (currentTrip.sharedTripId) {
    try {
      const state = await refreshSharedTripState(true);
      if (!state.latestItinerary && !activeItinerarySlots.length && currentTrip.initialItineraryStarted) {
        currentTrip.initialItineraryStarted = false;
      }
    } catch (error) { console.error("Could not load shared trip details:", error); }
  } else if (!activeItinerarySlots.length && currentTrip.initialItineraryStarted) {
    currentTrip.initialItineraryStarted = false;
  }
  renderGroupItineraryControls();
  startInitialItinerary();
  if (currentTrip.sharedTripId) startSharedItineraryPolling();
}

function startInitialItinerary() {
  if (!currentTrip) return;
  const promptField = $("#kpd-trip-prompt");
  const prompt = currentTrip.sharedTripId
    ? buildInitialItineraryPrompt((currentTrip.voteDirectives || []).join("\n"))
    : currentTrip.initialItineraryStarted
    ? currentTrip.originalPrompt || promptField.value.trim() || buildInitialItineraryPrompt()
    : buildInitialItineraryPrompt();
  if (!prompt) return;
  promptField.value = prompt;
  promptField.readOnly = true;
  currentTrip.originalPrompt = prompt;
  if (currentTrip.initialItineraryStarted) {
    saveUserData();
    return;
  }
  currentTrip.initialItineraryStarted = true;
  saveUserData();
  generateKpdItinerary(Boolean(currentTrip.sharedTripId));
}

let sharedItineraryTimer = null;

async function refreshSharedTripState(includeProfiles = false) {
  if (!currentTrip?.sharedTripId) return null;
  const suffix = includeProfiles ? "state" : "itinerary";
  const response = await fetch(`/api/shared-trips/${encodeURIComponent(currentTrip.sharedTripId)}/${suffix}`, {
    headers: { Authorization: `Bearer ${currentUser.sessionToken}` },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Could not refresh the shared trip.");
  currentTrip.memberCount = data.trip.memberCount;
  if (includeProfiles) {
    currentTrip.groupMembers = data.members || [];
    currentTrip.voteDirectives = data.voteDirectives || [];
  }
  currentTrip.sharedVotes = data.votes || currentTrip.sharedVotes || {};
  const sharedItinerary = data.latestItinerary;
  if (sharedItinerary && (!activeItinerarySlots.length || sharedItinerary.version > (currentTrip.sharedItineraryVersion || 0))) {
    currentTrip.sharedItineraryVersion = sharedItinerary.version;
    activeItinerarySlots = Array.isArray(sharedItinerary.slots) && sharedItinerary.slots.length
      ? sharedItinerary.slots
      : (Array.isArray(sharedItinerary.plan) ? sharedItinerary.plan : []);
    activeItineraryText = sharedItinerary.itinerary || "";
    if (sharedItinerary.basePrompt) {
      currentTrip.originalPrompt = sharedItinerary.basePrompt;
      $("#kpd-trip-prompt").value = sharedItinerary.basePrompt;
    }
    renderKpdItinerary();
  }
  saveUserData();
  renderGroupItineraryControls();
  return data;
}

function startSharedItineraryPolling() {
  if (sharedItineraryTimer) return;
  sharedItineraryTimer = window.setInterval(() => {
    if (document.visibilityState !== "visible" || !currentTrip?.sharedTripId) return;
    refreshSharedTripState(false).catch((error) => console.debug("Shared trip sync delayed:", error));
  }, 12000);
}

function renderGroupItineraryControls() {
  const controls = $("#group-itinerary-controls");
  if (!controls) return;
  const isGroup = Boolean(currentTrip?.sharedTripId);
  controls.hidden = !isGroup;
  if (isGroup) {
    const count = currentTrip.memberCount || 1;
    $("#group-itinerary-count").textContent = `Currently ${count} ${count === 1 ? "person" : "people"} in this trip.`;
  }
}

async function refreshGroupItinerary() {
  if (!currentTrip?.sharedTripId) return;
  const button = $("#kpd-refresh-group");
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = "Refreshing group details…";
  try {
    const data = await refreshSharedTripState(true);
    const prompt = buildInitialItineraryPrompt((data.voteDirectives || []).join("\n"));
    currentTrip.originalPrompt = prompt;
    $("#kpd-trip-prompt").value = prompt;
    saveUserData();
    await generateKpdItinerary(true);
  } catch (error) {
    $("#kpd-status").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

function enterApp(user, startPage = "plan") {
  currentUser = { name: user.name, email: user.email.toLowerCase(), sessionToken: user.sessionToken, interests: Array.isArray(user.interests) ? user.interests : [] };
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
  if (!interests.size) return { places: [], usedFallback: false };
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
  const interests = Array.isArray(currentUser?.interests) ? currentUser.interests : [];
  const city = form.get("city");
  const start = form.get("start");
  const end = form.get("end");
  const isGroupTrip = form.get("isGroupTrip") === "yes";

  if (!interests.length) { showToast("Add interests to your account to get recommendations."); return; }
  if (end < start) { showToast("Your leaving date needs to be after your arrival date."); return; }
  if (isGroupTrip) {
    showToast("Use Generate invite link to lock the dates and destination for your group.");
    return;
  }
  
  currentTrip = {
    id: makeTripId(), city, start, end, interests,
    likedPlaceIds: [], dislikedPlaceIds: [], shownPlaceIds: [], visiblePlaceIds: [],
  };
  planningNewTrip = false;
  userData.trips.unshift(currentTrip);
  saveUserData();
  $("#group-trip-invite").hidden = true;
  showPage("recommendations");
}

async function createGroupTrip() {
  const form = $("#trip-form");
  if (!form.reportValidity()) return;
  const interests = Array.isArray(currentUser?.interests) ? currentUser.interests : [];
  if (!interests.length) { showToast("Add interests to your account before creating a group trip."); return; }
  const city = $("#trip-city").value;
  const start = $("#trip-start").value;
  const end = $("#trip-end").value;
  if (!city || !start || !end) { showToast("Choose a destination and both trip dates first."); return; }
  if (end < start) { showToast("Your leaving date needs to be after your arrival date."); return; }
  currentTrip = {
    id: makeTripId(), city, start, end, interests,
    likedPlaceIds: [], dislikedPlaceIds: [], shownPlaceIds: [], visiblePlaceIds: [],
  };
  planningNewTrip = false;
  $("#group-trip-invite").hidden = false;
  await createInviteLink();
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

async function contributeSharedCandidates(trip = currentTrip) {
  if (!trip?.sharedTripId) return null;
  const candidateIds = matchingPlaces(trip, true).places
    .slice(0, 500)
    .map((place) => place.id);
  const result = await postJSON("/api/shared-trips/candidates", {
    token: trip.sharedTripId,
    candidateIds,
  });
  trip.sharedRecommendations = result.recommendations;
  saveUserData();
  return result.recommendations;
}

function makeInviteURL(token) {
  const url = new URL(window.location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("invite", token);
  return url.toString();
}

function renderGroupTripControls() {
  const checkbox = $("#is-group-trip");
  const invite = $("#group-trip-invite");
  if (!checkbox || !invite) return;
  const isLockedGroup = Boolean(currentTrip?.sharedTripId && !planningNewTrip);
  checkbox.checked = isLockedGroup;
  checkbox.disabled = isLockedGroup;
  if (currentTrip && !planningNewTrip) {
    $("#trip-city").value = currentTrip.city || "";
    $("#trip-start").value = currentTrip.start || "";
    $("#trip-end").value = currentTrip.end || "";
  } else if (planningNewTrip && currentTrip?.sharedTripId) {
    $("#trip-city").value = "";
    $("#trip-start").value = "";
    $("#trip-end").value = "";
  }
  [$("#trip-city"), $("#trip-start"), $("#trip-end")].forEach((field) => { field.disabled = isLockedGroup; });
  $("#group-trip-help").hidden = !checkbox.checked;
  $("#generate-group-trip").hidden = !checkbox.checked || isLockedGroup;
  $("#generate-group-trip").disabled = !$("#trip-city").value || !$("#trip-start").value || !$("#trip-end").value;
  $("#trip-submit").hidden = checkbox.checked;
  $("#trip-submit").textContent = "Find my places →";
  invite.hidden = !currentTrip?.sharedTripId || planningNewTrip;
  if (!currentTrip?.sharedTripId || planningNewTrip) return;
  const linkRow = $("#share-link-row");
  const count = $("#share-member-count");
  const linkInput = $("#share-link");
  linkInput.value = makeInviteURL(currentTrip.sharedTripId);
  linkRow.hidden = false;
  count.hidden = false;
  const memberCount = currentTrip.memberCount || 1;
  count.textContent = `${memberCount} ${memberCount === 1 ? "traveler" : "travelers"} on this trip`;
  $("#group-trip-status").textContent = "Your invite link is ready to share.";
  $("#view-group-recommendations").hidden = false;
}

async function createInviteLink() {
  if (!currentTrip || !currentUser) return;
  const status = $("#group-trip-status");
  const linkRow = $("#share-link-row");
  const count = $("#share-member-count");
  const viewButton = $("#view-group-recommendations");
  const generateButton = $("#generate-group-trip");
  const tripFields = [$("#trip-city"), $("#trip-start"), $("#trip-end")];
  tripFields.forEach((field) => { field.disabled = true; });
  generateButton.disabled = true;
  generateButton.textContent = "Creating invite link…";
  status.textContent = "Creating your invite link…";
  linkRow.hidden = true;
  count.hidden = true;
  viewButton.hidden = true;
  try {
    const result = await postJSON("/api/shared-trips", {
      city: currentTrip.city,
      start: currentTrip.start,
      end: currentTrip.end,
    });
    currentTrip.sharedTripId = result.trip.token;
    currentTrip.sharedOwnerName = result.trip.ownerName;
    currentTrip.memberCount = result.trip.memberCount;
    if (!userData.trips.includes(currentTrip)) userData.trips.unshift(currentTrip);
    saveUserData();
    try {
      await contributeSharedCandidates(currentTrip);
    } catch (error) {
      console.error("Could not add the owner's personalized places to the group pool:", error);
    }
    $("#share-link").value = makeInviteURL(currentTrip.sharedTripId);
    linkRow.hidden = false;
    count.hidden = false;
    count.textContent = `${currentTrip.memberCount} travelers on this trip`;
    viewButton.hidden = false;
    generateButton.hidden = true;
    status.textContent = "Your invite link is ready. Copy it and send it to your trip members.";
    renderGroupTripControls();
    showToast("Your trip invite link is ready to copy.");
  } catch (error) {
    tripFields.forEach((field) => { field.disabled = false; });
    generateButton.disabled = false;
    generateButton.textContent = "Generate invite link";
    status.textContent = error.message;
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
    banner.textContent = `${pendingInvite.ownerName} invited you to join a trip to ${pendingInvite.city}, ${formatDate(pendingInvite.start)} – ${formatDate(pendingInvite.end)}. The destination and dates are shared, and the itinerary will consider everyone’s signup interests.`;
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
        interests: currentUser.interests || [],
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
    trip.interests = currentUser.interests || [];
    trip.sharedTripId = shared.token;
    trip.sharedOwnerName = shared.ownerName;
    trip.memberCount = shared.memberCount;
    currentTrip = trip;
    saveUserData();
    try {
      await contributeSharedCandidates(trip);
    } catch (error) {
      console.error("Could not add personalized places to the group pool:", error);
    }
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
  $("#signup-tab").addEventListener("click", () => setAuthMode("signup"));
  $("#login-tab").addEventListener("click", () => setAuthMode("login"));
  $("#signup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name")).trim();
    const email = String(form.get("email")).trim().toLowerCase();
    const password = String(form.get("password"));
    const interests = [...document.querySelectorAll('#signup-interest-options input[name="interest"]:checked')]
      .map((input) => input.value.trim())
      .filter(Boolean);
    if (!interests.length) { showToast("Select at least one checked interest to create your account."); return; }
    try {
      const response = await fetch("/api/signup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email, password, interests }),
      });
      const result = await response.json();
      if (!response.ok) { showToast(result.error || "Could not create the account."); return; }
      await finishAuthentication({ ...result.user, sessionToken: result.sessionToken });
    } catch {
      showToast("Could not reach the local account server. Start it with python3 scripts/server.py.");
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
      showToast("Could not reach the local account server. Start it with python3 scripts/server.py.");
    }
  });
  $("#trip-form").addEventListener("submit", createTrip);
  $("#is-group-trip").addEventListener("change", (event) => {
    const checked = event.target.checked;
    $("#group-trip-help").hidden = !event.target.checked;
    $("#generate-group-trip").hidden = !checked;
    $("#trip-submit").hidden = checked;
    if (!currentTrip?.sharedTripId) $("#group-trip-invite").hidden = true;
    updateGroupTripButton();
  });
  $("#generate-group-trip").addEventListener("click", createGroupTrip);
  [$("#trip-city"), $("#trip-start"), $("#trip-end")].forEach((field) => {
    field.addEventListener("input", updateGroupTripButton);
    field.addEventListener("change", updateGroupTripButton);
  });
  $("#copy-share-link").addEventListener("click", copyInviteLink);
  $("#view-group-recommendations").addEventListener("click", () => showPage("recommendations"));
  $("#kpd-refresh-group").addEventListener("click", refreshGroupItinerary);
  $("#logout-button").addEventListener("click", signOut);
  $$("[data-page]").forEach((button) => button.addEventListener("click", () => showPage(button.dataset.page)));
  const today = new Date();
  const dateValue = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  $("#trip-start").min = dateValue;
  $("#trip-end").min = dateValue;
  $("#trip-start").addEventListener("change", (event) => {
    $("#trip-end").min = event.target.value || dateValue;
    updateGroupTripButton();
  });
}

function updateGroupTripButton() {
  const button = $("#generate-group-trip");
  if (!button) return;
  button.disabled = !$("#is-group-trip").checked ||
    !$("#trip-city").value || !$("#trip-start").value || !$("#trip-end").value ||
    $("#trip-end").value < $("#trip-start").value;
}

async function init() {
  bindEvents();
  // Render the known categories immediately; the CSV adds any new categories
  // when it loads, so a slow or unavailable file never leaves signup blank.
  categories = Object.keys(CATEGORY_LABELS).sort((a, b) =>
    (CATEGORY_LABELS[a] || a).localeCompare(CATEGORY_LABELS[b] || b)
  );
  renderInterests();
  try {
    await loadPlaces();
  } catch (error) {
    showToast("Couldn’t load the places CSV. Start the site from the project folder using a local web server.");
    console.error(error);
  }
  await loadInvite();
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


async function generateKpdItinerary(refreshGroup = false) {
  const generateButton = document.querySelector('#kpd-generate');
  if (generateButton.disabled) return;
  const originalPrompt = document.querySelector('#kpd-trip-prompt').value.trim();
  const changes = document.querySelector('#kpd-trip-changes').value.trim();
  const prompt = changes
    ? `${originalPrompt}\n\nTraveler's requested changes:\n${changes}`
    : originalPrompt;
  const status = document.querySelector('#kpd-status');
  const result = document.querySelector('#kpd-result');
  if (!prompt) { status.textContent = 'Describe your trip first.'; return; }
  generateButton.disabled = true;
  generateButton.textContent = 'Building your itinerary…';
  status.textContent = refreshGroup || activeItinerarySlots.length
    ? 'Hold tight, your itinerary is being updated…'
    : 'Hold tight, your itinerary is being generated…';
  result.replaceChildren();
  try {
    const body = await postJSON('/api/itineraries', {
      prompt, basePrompt: originalPrompt, sharedTripId: currentTrip?.sharedTripId || null, refreshGroup
    });
    const response = await pollKpdJob(body.job_id);
    if (response.status !== 'completed') throw new Error(response.error || 'Planner did not finish.');
    const returnedSlots = Array.isArray(response.slots) && response.slots.length
      ? response.slots
      : (Array.isArray(response.plan) ? response.plan : []);
    if (!returnedSlots.length) throw new Error('The planner completed without returning any scheduled days.');
    status.textContent = 'Your itinerary is ready.';
    activeItinerarySlots = returnedSlots;
    activeItineraryText = response.itinerary || '';
    renderKpdItinerary();
  } catch (error) {
    status.textContent = friendlyItineraryError(error);
    result.replaceChildren();
  }
  finally {
    generateButton.disabled = false;
    generateButton.textContent = 'Generate itinerary';
  }
}

function friendlyItineraryError(error) {
  const message = String(error?.message || error || 'The itinerary could not be generated.');
  if (/Traceback|ValueError:|RuntimeError:|Neo4j candidates:|CP-SAT returned|No candidate for named/i.test(message)) {
    return 'The planner could not satisfy every requested activity for this destination and date range. Your current timetable is still available; try changing the requests and generating again.';
  }
  return message.length > 260 ? `${message.slice(0, 257)}…` : message;
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

document.querySelector('#kpd-generate').addEventListener('click', () => {
  if (currentTrip?.sharedTripId && currentTrip.initialItineraryStarted) refreshGroupItinerary();
  else generateKpdItinerary(false);
});