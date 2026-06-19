// Consent + payment page for the booking URL-mode elicitation (Steps 4–6 of the
// MCP Dev Summit demo). Shows the booking summary, forces an AM OIDC sign-in
// (with whatever MFA / step-up AM enforces), collects consent, then hands the
// authenticated identity back to the chat window which resolves the elicitation.

const cfg = {
    oidcUrl: window.APP_CONFIG?.oidcUrl,
    clientId: window.APP_CONFIG?.clientId || 'acme-hotels',
    redirectUri: window.location.origin + '/consent.html',
};

const $ = (id) => document.getElementById(id);
const q = new URLSearchParams(window.location.search);

// --- booking summary -------------------------------------------------------
function renderSummary(b) {
    $('sumHotel').textContent = b.hotel_name || b.hotel_id || 'Hotel';
    $('sumRoom').textContent = b.room_type || '—';
    $('sumIn').textContent = b.check_in || '—';
    $('sumOut').textContent = b.check_out || '—';
    $('sumGuests').textContent = b.guests || '—';
    const nights = b.nights || '—';
    $('sumNights').textContent = nights;
    const total = parseFloat(b.total_price || 0);
    const ppn = parseFloat(b.price_per_night || 0);
    $('sumNightsK').textContent = ppn ? `Nights ($${ppn}/night)` : 'Nights';
    $('sumTotal').textContent = total ? `$${total.toFixed(2)}` : '—';
}

// --- PKCE helpers (mirrors the main app) -----------------------------------
function b64url(buf) {
    return btoa(String.fromCharCode(...buf)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}
function randVerifier() { const a = new Uint8Array(32); crypto.getRandomValues(a); return b64url(a); }
async function challenge(v) {
    const h = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(v));
    return b64url(new Uint8Array(h));
}
function randStr(n) { const a = new Uint8Array(n); crypto.getRandomValues(a); return Array.from(a, x => x.toString(16).padStart(2, '0')).join(''); }
function parseJwt(t) {
    const p = t.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(decodeURIComponent(atob(p).split('').map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join('')));
}

async function loadOidc() {
    const r = await fetch(cfg.oidcUrl);
    if (!r.ok) throw new Error('Failed to load OIDC config');
    return r.json();
}

// --- sign-in (redirect within the popup) -----------------------------------
async function startSignin(booking) {
    const oidc = await loadOidc();
    const verifier = randVerifier();
    sessionStorage.setItem('consent_pkce', verifier);
    sessionStorage.setItem('consent_state', randStr(16));
    sessionStorage.setItem('consent_booking', JSON.stringify(booking));
    const params = new URLSearchParams({
        client_id: cfg.clientId,
        redirect_uri: cfg.redirectUri,
        response_type: 'code',
        scope: 'openid profile email',
        code_challenge: await challenge(verifier),
        code_challenge_method: 'S256',
        state: sessionStorage.getItem('consent_state'),
    });
    window.location.href = `${oidc.authorization_endpoint}?${params.toString()}`;
}

async function completeSignin(code) {
    const oidc = await loadOidc();
    const verifier = sessionStorage.getItem('consent_pkce');
    const body = new URLSearchParams({
        grant_type: 'authorization_code',
        code,
        redirect_uri: cfg.redirectUri,
        client_id: cfg.clientId,
        code_verifier: verifier,
    });
    const r = await fetch(oidc.token_endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
    });
    if (!r.ok) throw new Error('Token exchange failed: ' + (await r.text()));
    const tok = await r.json();
    if (!tok.id_token) throw new Error('No id_token (need openid profile email scopes)');
    const claims = parseJwt(tok.id_token);
    const name = claims.name || [claims.given_name, claims.family_name].filter(Boolean).join(' ') || claims.preferred_username || claims.email;
    return { guest_name: name, guest_email: claims.email, access_token: tok.access_token };
}

// --- consent step ----------------------------------------------------------
function showConsent(profile, booking) {
    $('stepSignin').classList.add('hidden');
    $('stepConsent').classList.remove('hidden');
    $('pname').textContent = profile.guest_name || 'User';
    $('pemail').textContent = profile.guest_email || '';
    $('pavatar').textContent = (profile.guest_name || 'U').trim()[0].toUpperCase();
    const chk = $('consentChk'), btn = $('confirmBtn');
    chk.addEventListener('change', () => { btn.disabled = !chk.checked; });
    btn.addEventListener('click', () => finish(profile, booking));
}

function gatewayOrigin() {
    try { return new URL(window.APP_CONFIG.agentCardUrl).origin; }
    catch { return 'http://localhost:8082'; }
}

async function finish(profile, booking) {
    $('confirmBtn').disabled = true;
    $('confirmBtn').textContent = 'Processing payment…';
    // The booking/payment is finalised out-of-band here, with the user's real
    // identity and access token, through the gateway (URL-mode elicitation per
    // the MCP spec keeps sensitive data out of the LLM/agent path).
    let booked = null;
    try {
        const r = await fetch(gatewayOrigin() + '/hotels/bookings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(profile.access_token ? { 'Authorization': 'Bearer ' + profile.access_token } : {}),
                'X-User-Email': profile.guest_email || '',
            },
            body: JSON.stringify({
                hotel_id: booking.hotel_id,
                guest_name: profile.guest_name,
                guest_email: profile.guest_email,
                room_type: booking.room_type,
                check_in: booking.check_in,
                check_out: booking.check_out,
                guests: parseInt(booking.guests || '1', 10),
            }),
        });
        if (r.ok) booked = await r.json();
        else console.error('Booking failed', r.status, await r.text());
    } catch (e) {
        console.error('Booking error', e);
    }

    // Resolve the MCP URL-elicitation so the agent's createBooking returns.
    const payload = {
        type: 'elicitation-consent',
        eid: booking.eid,
        action: 'accept',
        content: { guest_name: profile.guest_name, guest_email: profile.guest_email },
        booking: booked,
    };
    if (window.opener && !window.opener.closed) {
        window.opener.postMessage(payload, window.location.origin);
    }
    $('stepConsent').classList.add('hidden');
    $('stepDone').classList.remove('hidden');
    setTimeout(() => window.close(), 1800);
}

// --- bootstrap -------------------------------------------------------------
(async function init() {
    const code = q.get('code');
    if (code) {
        // OIDC callback: restore booking + complete sign-in.
        const booking = JSON.parse(sessionStorage.getItem('consent_booking') || '{}');
        renderSummary(booking);
        try {
            const profile = await completeSignin(code);
            // clean the code from the URL
            window.history.replaceState({}, document.title, '/consent.html');
            showConsent(profile, booking);
        } catch (e) {
            alert('Sign-in failed: ' + e.message);
        }
        return;
    }
    // First load: render summary from query params, wire sign-in.
    const booking = {
        eid: q.get('eid'), hotel_id: q.get('hotel_id'), hotel_name: q.get('hotel_name'),
        room_type: q.get('room_type'), check_in: q.get('check_in'), check_out: q.get('check_out'),
        guests: q.get('guests'), nights: q.get('nights'),
        price_per_night: q.get('price_per_night'), total_price: q.get('total_price'),
    };
    renderSummary(booking);
    $('signinBtn').addEventListener('click', () => startSignin(booking).catch(e => alert('Sign-in error: ' + e.message)));
})();
