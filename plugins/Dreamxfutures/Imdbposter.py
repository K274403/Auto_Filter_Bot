import re
import aiohttp
import warnings
import logging
from io import BytesIO
from PIL import Image

from info import DREAMXBOTZ_IMAGE_FETCH, TMDB_API_KEY
from imdb import Cinemagoer


logger = logging.getLogger(__name__)

# IMDbPY / Cinemagoer fallback remains available.
ia = Cinemagoer(accessSystem="https")
LONG_IMDB_DESCRIPTION = False

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

_session: aiohttp.ClientSession | None = None

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w1280"


async def get_session():
    """Return one reusable aiohttp session for the module."""
    global _session

    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=20)
        _session = aiohttp.ClientSession(timeout=timeout)

    return _session


async def fetch_image(url, size=(860, 1200)):
    """Download and optionally resize an image for Telegram."""
    if not url:
        return None

    if not DREAMXBOTZ_IMAGE_FETCH:
        logger.info("Image fetching is disabled.")
        return url

    try:
        session = await get_session()

        async with session.get(url) as response:
            if response.status != 200:
                logger.error(
                    "Failed to fetch image: %s for %s",
                    response.status,
                    url,
                )
                return None

            data = await response.read()

        img = Image.open(BytesIO(data))
        img = img.convert("RGB")
        img = img.resize(size, Image.LANCZOS)

        out = BytesIO()
        img.save(out, format="JPEG", quality=95)
        out.seek(0)
        return out

    except aiohttp.ClientError as e:
        logger.error("HTTP request error in fetch_image: %s", e)
    except (IOError, OSError) as e:
        logger.error("Image I/O error in fetch_image: %s", e)
    except Exception as e:
        logger.exception("Unexpected error in fetch_image: %s", e)

    return None


async def close_session():
    """Close the shared aiohttp session cleanly."""
    global _session

    if _session and not _session.closed:
        await _session.close()

    _session = None


def list_to_str(lst):
    if lst:
        return ", ".join(map(str, lst))
    return ""


def _extract_year(query: str, file: str | None = None):
    """Extract a 4-digit release year from query/file name."""
    years = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", query or "")

    if not years and file:
        years = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", file)

    return int(years[0]) if years else None


def _clean_title(query: str):
    """Remove a trailing year before sending the title to TMDB/IMDb."""
    title = str(query or "").strip()
    title = re.sub(r"[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]\s*$", "", title)
    title = re.sub(r"\s+(?:19|20)\d{2}\s*$", "", title)
    return title.strip()


def _tmdb_image(path: str | None, size: str = "w1280"):
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"https://image.tmdb.org/t/p/{size}{path}"


def _first_name(items):
    if not items:
        return None

    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                return item["name"]

    return None


async def get_movie_details(query, id=False, file=None):
    """
    IMDbPY/Cinemagoer fallback.

    This function intentionally returns None on failure because the caller
    can decide how to skip/fallback.
    """
    try:
        if not id:
            query = str(query).strip().lower()
            title = query
            year = _extract_year(query, file)

            if year:
                title = _clean_title(query)

            movie_results = ia.search_movie(title.lower(), results=10)
            if not movie_results:
                return None

            if year:
                filtered = [
                    item
                    for item in movie_results
                    if str(item.get("year")) == str(year)
                ]
                if not filtered:
                    filtered = movie_results
            else:
                filtered = movie_results

            filtered_kind = [
                item
                for item in filtered
                if item.get("kind") in ("movie", "tv series", "tv mini series")
            ]

            if not filtered_kind:
                logger.info(
                    "No movie/TV match found for '%s'; using best IMDb result.",
                    query,
                )
                filtered_kind = filtered

            movieid = getattr(filtered_kind[0], "movieID", None)
            if not movieid:
                return None
        else:
            movieid = str(query).replace("tt", "").strip()

        movie = ia.get_movie(movieid)
        if not movie:
            return None

        ia.update(movie, info=["main", "vote details"])

        if movie.get("original air date"):
            date = movie["original air date"]
        elif movie.get("year"):
            date = movie.get("year")
        else:
            date = "N/A"

        plot = movie.get("plot")
        if plot and isinstance(plot, list):
            plot = plot[0]
        else:
            plot = movie.get("plot outline")

        if plot and len(plot) > 800:
            plot = plot[:800] + "..."

        poster_url = movie.get("full-size cover url")
        if poster_url and poster_url.endswith("@.jpg"):
            poster_url += "._V1_SX1440.jpg"

        imdb_id = movie.get("imdbID")

        return {
            "title": movie.get("title"),
            "votes": movie.get("votes"),
            "aka": list_to_str(movie.get("akas")),
            "seasons": movie.get("number of seasons"),
            "box_office": movie.get("box office"),
            "localized_title": movie.get("localized title"),
            "kind": movie.get("kind"),
            "imdb_id": f"tt{imdb_id}" if imdb_id else None,
            "cast": list_to_str(movie.get("cast")),
            "runtime": list_to_str(movie.get("runtimes")),
            "countries": list_to_str(movie.get("countries")),
            "certificates": list_to_str(movie.get("certificates")),
            "languages": list_to_str(movie.get("languages")),
            "director": list_to_str(movie.get("director")),
            "writer": list_to_str(movie.get("writer")),
            "producer": list_to_str(movie.get("producer")),
            "composer": list_to_str(movie.get("composer")),
            "cinematographer": list_to_str(movie.get("cinematographer")),
            "music_team": list_to_str(movie.get("music department")),
            "distributors": list_to_str(movie.get("distributors")),
            "release_date": date,
            "year": movie.get("year"),
            "genres": list_to_str(movie.get("genres")),
            "poster_url": poster_url,
            "backdrop_url": None,
            "plot": plot,
            "rating": str(movie.get("rating", "N/A")),
            "url": (
                f"https://www.imdb.com/title/tt{movieid}"
                if movieid
                else ""
            ),
            "tmdb_url": None,
        }

    except Exception as e:
        logger.exception("An error occurred in get_movie_details: %s", e)
        return None


async def _tmdb_get(session, endpoint, params):
    """GET a TMDB endpoint and safely decode its JSON response."""
    url = f"{TMDB_BASE_URL}/{endpoint.lstrip('/')}"

    try:
        async with session.get(
            url,
            params=params,
            headers={"accept": "application/json"},
        ) as resp:
            body = await resp.text()

            if resp.status != 200:
                logger.error(
                    "TMDB request failed [%s] endpoint=%s response=%s",
                    resp.status,
                    endpoint,
                    body[:500],
                )
                return None

            content_type = resp.headers.get("Content-Type", "").lower()
            if "json" not in content_type:
                logger.error(
                    "TMDB returned unexpected Content-Type '%s' endpoint=%s",
                    content_type,
                    endpoint,
                )
                return None

            try:
                return await resp.json(content_type=None)
            except Exception:
                logger.error(
                    "TMDB returned invalid JSON endpoint=%s body=%s",
                    endpoint,
                    body[:500],
                )
                return None

    except aiohttp.ClientError as e:
        logger.error("TMDB network error [%s] endpoint=%s", e, endpoint)
        return None
    except Exception as e:
        logger.exception("Unexpected TMDB error endpoint=%s: %s", endpoint, e)
        return None


async def get_movie_detailsx(query, id=False, file=None):
    """
    Fetch movie/TV metadata directly from TMDB.

    The previous bharath-boy-api Vercel proxy has been removed because the
    deployment currently returns HTTP 402 / DEPLOYMENT_DISABLED. The function
    returns a dictionary with 'error' on configuration/API failure so callers
    never receive None unexpectedly.
    """
    q = str(query or "").strip()

    if not q:
        return {
            "error": "Empty movie query",
            "poster_url": None,
            "backdrop_url": None,
        }

    api_key = str(TMDB_API_KEY or "").strip()
    if not api_key:
        logger.error(
            "TMDB_API_KEY is empty. TMDB poster lookup is disabled; "
            "caller should use the IMDb fallback."
        )
        return {
            "error": "TMDB_API_KEY is not configured",
            "poster_url": None,
            "backdrop_url": None,
        }

    year = _extract_year(q, file)
    title = _clean_title(q)

    try:
        session = await get_session()

        # First try movies, then TV shows. This handles both movie and series
        # filenames without relying on the retired third-party proxy.
        candidates = []

        search_params = {
            "api_key": api_key,
            "query": title or q,
            "include_adult": "false",
            "language": "en-US",
            "page": 1,
        }
        if year:
            search_params["year"] = year

        movie_search = await _tmdb_get(session, "/search/movie", search_params)
        if movie_search:
            for item in movie_search.get("results", []):
                if isinstance(item, dict):
                    item["media_type"] = "movie"
                    candidates.append(item)

        tv_params = {
            "api_key": api_key,
            "query": title or q,
            "include_adult": "false",
            "language": "en-US",
            "page": 1,
        }
        if year:
            tv_params["first_air_date_year"] = year

        tv_search = await _tmdb_get(session, "/search/tv", tv_params)
        if tv_search:
            for item in tv_search.get("results", []):
                if isinstance(item, dict):
                    item["media_type"] = "tv"
                    candidates.append(item)

        if not candidates:
            return {
                "error": f"No TMDB result found for '{q}'",
                "poster_url": None,
                "backdrop_url": None,
            }

        # Prefer an exact year match, then popularity.
        def candidate_year(item):
            date = item.get("release_date") or item.get("first_air_date") or ""
            try:
                return int(date[:4])
            except (TypeError, ValueError):
                return None

        exact_year = (
            [item for item in candidates if candidate_year(item) == year]
            if year
            else []
        )
        pool = exact_year or candidates
        pool.sort(key=lambda item: float(item.get("popularity", 0) or 0), reverse=True)
        selected = pool[0]

        media_type = selected.get("media_type", "movie")
        tmdb_id = selected.get("id")

        if not tmdb_id:
            return {
                "error": "TMDB result did not contain an ID",
                "poster_url": None,
                "backdrop_url": None,
            }

        details_endpoint = f"/{media_type}/{tmdb_id}"
        detail_params = {
            "api_key": api_key,
            "language": "en-US",
            "append_to_response": "credits,external_ids",
        }
        data = await _tmdb_get(session, details_endpoint, detail_params)

        if not isinstance(data, dict):
            return {
                "error": f"TMDB details request failed for '{q}'",
                "poster_url": None,
                "backdrop_url": None,
            }

        credits = data.get("credits") or {}
        crew = credits.get("crew") or []
        cast_items = credits.get("cast") or []

        directors = [
            item.get("name")
            for item in crew
            if item.get("job") == "Director" and item.get("name")
        ]
        writers = [
            item.get("name")
            for item in crew
            if item.get("department") == "Writing" and item.get("name")
        ]
        producers = [
            item.get("name")
            for item in crew
            if item.get("job") in ("Producer", "Executive Producer")
            and item.get("name")
        ]
        composers = [
            item.get("name")
            for item in crew
            if item.get("job") in ("Original Music Composer", "Music")
            and item.get("name")
        ]
        cinematographers = [
            item.get("name")
            for item in crew
            if item.get("job") == "Director of Photography" and item.get("name")
        ]
        cast_names = [
            item.get("name")
            for item in cast_items[:15]
            if item.get("name")
        ]

        if media_type == "tv":
            release_date = data.get("first_air_date")
            runtime = data.get("episode_run_time") or []
            seasons = data.get("number_of_seasons")
            title_value = data.get("name") or data.get("original_name")
            external_imdb_id = (data.get("external_ids") or {}).get("imdb_id")
            tmdb_url = f"https://www.themoviedb.org/tv/{tmdb_id}"
        else:
            release_date = data.get("release_date")
            runtime = [data.get("runtime")] if data.get("runtime") else []
            seasons = None
            title_value = data.get("title") or data.get("original_title")
            external_imdb_id = (data.get("external_ids") or {}).get("imdb_id")
            tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"

        genres = [
            item.get("name")
            for item in data.get("genres", [])
            if isinstance(item, dict) and item.get("name")
        ]
        countries = [
            item.get("name")
            for item in data.get(
                "production_countries",
                data.get("origin_country", []),
            )
            if isinstance(item, dict) and item.get("name")
        ]
        if not countries:
            countries = data.get("origin_country", []) or []

        languages = [
            item.get("english_name") or item.get("iso_639_1")
            for item in data.get("spoken_languages", [])
            if isinstance(item, dict)
        ]

        imdb_url = (
            f"https://www.imdb.com/title/{external_imdb_id}"
            if external_imdb_id
            else None
        )

        return {
            "title": title_value,
            "localized_title": title_value,
            "year": (
                int(release_date[:4])
                if release_date and len(release_date) >= 4
                and release_date[:4].isdigit()
                else selected.get("release_date", "")[:4]
                if selected.get("release_date")
                and selected.get("release_date", "")[:4].isdigit()
                else None
            ),
            "release_date": release_date,
            "rating": (
                round(float(data.get("vote_average", 0) or 0), 1)
                if data.get("vote_average") is not None
                else None
            ),
            "votes": int(data.get("vote_count", 0) or 0),
            "runtime": list_to_str(runtime),
            "certificates": None,
            "tmdb_url": tmdb_url,
            "url": imdb_url or tmdb_url,
            "plot": data.get("overview"),
            "tagline": data.get("tagline"),
            "box_office": data.get("revenue") if data.get("revenue") else None,
            "distributors": list_to_str(
                [
                    company.get("name")
                    for company in data.get("production_companies", [])
                    if isinstance(company, dict) and company.get("name")
                ]
            ),
            "imdb_id": external_imdb_id,
            "tmdb_id": str(tmdb_id),
            "kind": "tv series" if media_type == "tv" else "movie",
            "seasons": seasons,
            "genres": list_to_str(genres),
            "languages": list_to_str(languages),
            "countries": list_to_str(countries),
            "director": list_to_str(directors),
            "writer": list_to_str(writers),
            "producer": list_to_str(producers),
            "composer": list_to_str(composers),
            "cinematographer": list_to_str(cinematographers),
            "cast": list_to_str(cast_names),
            "poster_url": _tmdb_image(data.get("poster_path")),
            "backdrop_url": _tmdb_image(data.get("backdrop_path")),
        }

    except Exception as e:
        logger.exception("An error occurred in get_movie_detailsx: %s", e)
        return {
            "error": str(e),
            "poster_url": None,
            "backdrop_url": None,
        }
