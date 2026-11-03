import os
import re
import secrets
import threading
import time
from functools import wraps
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

SPOTIFY_ACCOUNTS_URL = "https://accounts.spotify.com"
SPOTIFY_API_URL = "https://api.spotify.com/v1"
OAUTH_SCOPES = "user-library-read playlist-read-private playlist-read-collaborative"
SCAN_JOBS = {}
SCAN_JOBS_LOCK = threading.Lock()


def spotify_configured():
    return bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "access_token" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def spotify_request_legacy(path_or_url, method="GET", access_token=None, **kwargs):
    url = path_or_url if path_or_url.startswith("http") else f"{SPOTIFY_API_URL}{path_or_url}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {session['access_token']}"
    last_error = None
    for attempt in range(2):
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                timeout=(10, 30),
                **kwargs,
            )
            break
        except requests.RequestException as error:
            last_error = error
            if attempt == 0:
                time.sleep(1)
    else:
        raise RuntimeError(
            "Spotify could not be reached. Check your internet connection and try again."
        ) from last_error

    if response.status_code == 401:
        session.clear()
        raise RuntimeError("Your Spotify session expired. Please sign in again.")
    if not response.ok:
        try:
            message = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            message = response.text
        raise RuntimeError(f"Spotify returned an error: {message}")
    return response.json()


def spotify_request(path_or_url, method="GET", access_token=None, **kwargs):
    url = path_or_url if path_or_url.startswith("http") else f"{SPOTIFY_API_URL}{path_or_url}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {access_token or session['access_token']}"
    last_error = None
    for attempt in range(2):
        try:
            response = requests.request(method, url, headers=headers, timeout=(10, 30), **kwargs)
            break
        except requests.RequestException as error:
            last_error = error
            if attempt == 0:
                time.sleep(1)
    else:
        raise RuntimeError(
            "Spotify could not be reached. Check your internet connection and try again."
        ) from last_error
    if response.status_code == 401:
        raise RuntimeError("Your Spotify session expired. Please sign in again.")
    if not response.ok:
        try:
            message = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            message = response.text
        raise RuntimeError(f"Spotify returned an error: {message}")
    return response.json()


def fetch_all_pages(path, access_token=None, progress=None):
    items = []
    next_url = path
    while next_url:
        page = spotify_request(next_url, access_token=access_token)
        items.extend(page.get("items", []))
        next_url = page.get("next")
        if progress:
            progress(len(items))
    return items


def track_from_item(item):
    track = item.get("track") or item
    if not track or track.get("type") != "track":
        return None
    artists = track.get("artists") or []
    if not track.get("name") or not artists:
        return None
    return {
        "name": track["name"],
        "artist": ", ".join(artist["name"] for artist in artists),
        "album": (track.get("album") or {}).get("name", ""),
        "url": (track.get("external_urls") or {}).get("spotify", ""),
    }


def normalise(value):
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


RELEASE_SUFFIX = (
    r"(?:"
    r"(?:\d{4}\s+)?"
    r"(?:remaster(?:ed)?|deluxe|anniversary|expanded|special|"
    r"collector'?s?|original|mono|stereo|acoustic|live|radio|single|"
    r"album|bonus|edit|mix|version|re-recorded|reimagined|edition)"
    r"(?:\s+(?:edition|version|mix|edit|recording|remaster(?:ed)?))?"
    r"(?:\s+\d{4})?"
    r")"
)

def release_independent_title(value):
    title = normalise(value)
    while True:
        without_suffix = re.sub(rf"\s+{RELEASE_SUFFIX}$", "", title).strip()
        if without_suffix == title:
            return title
        title = without_suffix


def find_duplicates(tracks):
    groups = []
    for track in tracks:
        title = release_independent_title(track["name"])
        artist = normalise(track["artist"])
        matching_group = next(
            (
                group
                for group in groups
                if group["artist_key"] == artist and group["title_key"] == title
            ),
            None,
        )
        if matching_group:
            matching_group["tracks"].append(track)
        else:
            groups.append({"artist_key": artist, "title_key": title, "tracks": [track]})
    return [group["tracks"] for group in groups if len(group["tracks"]) > 1]


def track_key(track):
    return (release_independent_title(track["name"]), normalise(track["artist"]))


def find_unfiled_tracks(liked_tracks, playlist_tracks):
    playlist_keys = {track_key(track) for track in playlist_tracks}
    return [track for track in liked_tracks if track_key(track) not in playlist_keys]


def find_unliked_tracks(playlist_tracks, liked_tracks):
    liked_keys = {track_key(track) for track in liked_tracks}
    return [track for track in playlist_tracks if track_key(track) not in liked_keys]


def find_unliked_playlist_songs(playlist_track_entries, liked_tracks):
    liked_keys = {track_key(track) for track in liked_tracks}
    songs = {}
    for playlist_name, track in playlist_track_entries:
        if track_key(track) in liked_keys:
            continue
        key = track_key(track)
        song = songs.setdefault(key, {"track": track, "playlists": set()})
        song["playlists"].add(playlist_name)
    return [
        {"track": song["track"], "playlists": sorted(song["playlists"])}
        for song in songs.values()
    ]


def find_repeated_playlist_songs(playlist_tracks):
    songs = {}
    for playlist_name, track in playlist_tracks:
        key = track_key(track)
        song = songs.setdefault(
            key,
            {"track": track, "playlists": set()},
        )
        song["playlists"].add(playlist_name)
    return [
        {"track": song["track"], "playlists": sorted(song["playlists"])}
        for song in songs.values()
        if len(song["playlists"]) > 1
    ]


@app.route("/")
def index():
    return render_template("index.html", logged_in="access_token" in session)


@app.route("/login")
def login():
    if not spotify_configured():
        flash("Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to .env first.", "error")
        return redirect(url_for("index"))
    session["oauth_state"] = secrets.token_urlsafe(24)
    params = {
        "client_id": os.environ["SPOTIFY_CLIENT_ID"],
        "response_type": "code",
        "redirect_uri": url_for("callback", _external=True),
        "scope": OAUTH_SCOPES,
        "state": session["oauth_state"],
    }
    return redirect(f"{SPOTIFY_ACCOUNTS_URL}/authorize?{urlencode(params)}")


@app.route("/callback")
def callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Spotify sign-in could not be verified.", "error")
        return redirect(url_for("index"))
    if "error" in request.args:
        flash(f"Spotify sign-in failed: {request.args['error']}", "error")
        return redirect(url_for("index"))
    try:
        response = requests.post(
            f"{SPOTIFY_ACCOUNTS_URL}/api/token",
            data={
                "grant_type": "authorization_code",
                "code": request.args["code"],
                "redirect_uri": url_for("callback", _external=True),
            },  
            auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
            timeout=30,
        )
        response.raise_for_status()
        token = response.json()
    except (requests.RequestException, KeyError, ValueError) as error:
        flash(f"Could not complete Spotify sign-in: {error}", "error")
        return redirect(url_for("index"))
    access_token = token.get("access_token")
    if not access_token:
        flash("Spotify did not return an access token. Please try again.", "error")
        return redirect(url_for("index"))
    session["access_token"] = access_token
    session["refresh_token"] = token.get("refresh_token")
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/scan", methods=["POST"])
@login_required
def scan():
    source = request.form.get("source")
    playlist_id = request.form.get("playlist_id", "").strip()
    if source not in {
        "liked",
        "playlist",
        "all_playlists",
        "owned_duplicates",
        "owned_repeated",
        "owned_unliked",
    }:
        flash("Choose liked songs or enter a playlist ID.", "error")
        return redirect(url_for("index"))
    if source == "playlist" and not re.fullmatch(
        r"[A-Za-z0-9]+", playlist_id
    ):
        flash("Choose a valid playlist ID.", "error")
        return redirect(url_for("index"))
    job_id = secrets.token_urlsafe(18)
    with SCAN_JOBS_LOCK:
        SCAN_JOBS[job_id] = {
            "status": "running",
            "message": "Starting Spotify check…",
            "access_token": session["access_token"],
        }
    threading.Thread(
        target=run_scan,
        args=(job_id, source, playlist_id, session["access_token"]),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


def update_scan(job_id, message):
    with SCAN_JOBS_LOCK:
        if job_id in SCAN_JOBS:
            SCAN_JOBS[job_id]["message"] = message


def run_scan(job_id, source, playlist_id, access_token):
    try:
        if source in {
            "all_playlists",
            "owned_duplicates",
            "owned_repeated",
            "owned_unliked",
        }:
            update_scan(job_id, "Loading your Spotify profile…")
            user = spotify_request("/me", access_token=access_token)
            update_scan(job_id, "Loading your playlists…")
            all_playlists = fetch_all_pages("/me/playlists?limit=50", access_token)
            playlists = [
                playlist
                for playlist in all_playlists
                if (playlist.get("owner") or {}).get("id") == user.get("id")
            ]
            update_scan(job_id, f"Found {len(playlists)} playlists owned by you.")
            playlist_tracks = []
            playlist_track_entries = []
            total = len(playlists)
            for index, playlist in enumerate(playlists, 1):
                update_scan(job_id, f"Checking playlist {index} of {total}: {playlist.get('name', 'Untitled')}")
                tracks = [
                    track
                    for item in fetch_all_pages(
                        f"/playlists/{playlist['id']}/items?limit=50",
                        access_token,
                    )
                    if (track := track_from_item(item))
                ]
                playlist_tracks.extend(tracks)
                playlist_track_entries.extend(
                    (playlist.get("name", "Untitled"), track) for track in tracks
                )
            update_scan(job_id, "Comparing song titles and artists…")
            if source == "owned_duplicates":
                result = {
                    "duplicates": find_duplicates(playlist_tracks),
                    "source_name": "Duplicates across your owned playlists",
                    "track_count": len(playlist_tracks),
                }
            elif source == "owned_repeated":
                result = {
                    "repeated": find_repeated_playlist_songs(playlist_track_entries),
                    "source_name": "Songs repeated across your owned playlists",
                    "track_count": len(playlist_tracks),
                }
            elif source == "owned_unliked":
                update_scan(job_id, "Loading liked songs…")
                liked_items = fetch_all_pages(
                    "/me/tracks?limit=50",
                    access_token,
                    lambda count: update_scan(job_id, f"Loading liked songs ({count} loaded)…"),
                )
                liked_tracks = [track for item in liked_items if (track := track_from_item(item))]
                result = {
                    "unliked": find_unliked_playlist_songs(
                        playlist_track_entries,
                        liked_tracks,
                    ),
                    "playlist_count": total,
                    "source_name": "Owned playlist songs not in liked songs",
                    "track_count": len(playlist_tracks),
                }
            else:
                update_scan(job_id, "Loading liked songs…")
                liked_items = fetch_all_pages(
                    "/me/tracks?limit=50",
                    access_token,
                    lambda count: update_scan(job_id, f"Loading liked songs ({count} loaded)…"),
                )
                liked_tracks = [track for item in liked_items if (track := track_from_item(item))]
                result = {
                    "missing": find_unfiled_tracks(liked_tracks, playlist_tracks),
                    "playlist_count": total,
                    "source_name": "Uncategorized liked songs",
                    "track_count": len(liked_tracks),
                }
        else:
            endpoint = "/me/tracks?limit=50"
            source_name = "Liked songs"
            if source == "playlist":
                endpoint = f"/playlists/{playlist_id}/items?limit=50&fields=items(track(name,artists,album,external_urls,type)),next"
                source_name = f"Playlist {playlist_id}"
            update_scan(job_id, "Loading songs from Spotify…")
            items = fetch_all_pages(
                endpoint,
                access_token,
                lambda count: update_scan(job_id, f"Loading songs ({count} loaded)…"),
            )
            tracks = [track for item in items if (track := track_from_item(item))]
            update_scan(job_id, "Comparing song titles and artists…")
            result = {
                "duplicates": find_duplicates(tracks),
                "source_name": source_name,
                "track_count": len(tracks),
            }
        with SCAN_JOBS_LOCK:
            SCAN_JOBS[job_id].update(status="complete", message="Check complete.", result=result)
    except (KeyError, RuntimeError) as error:
        with SCAN_JOBS_LOCK:
            SCAN_JOBS[job_id].update(status="error", message=str(error))


@app.route("/scan-status/<job_id>")
@login_required
def scan_status(job_id):
    with SCAN_JOBS_LOCK:
        job = SCAN_JOBS.get(job_id)
        if not job or job["access_token"] != session["access_token"]:
            return jsonify({"status": "error", "message": "Scan not found."}), 404
        return jsonify({"status": job["status"], "message": job["message"]})


@app.route("/scan-results/<job_id>")
@login_required
def scan_results(job_id):
    with SCAN_JOBS_LOCK:
        job = SCAN_JOBS.get(job_id)
        if not job or job["access_token"] != session["access_token"] or job["status"] != "complete":
            flash("That scan is no longer available.", "error")
            return redirect(url_for("index"))
        result = job["result"]
    return render_template("results.html", logged_in=True, **result)


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
