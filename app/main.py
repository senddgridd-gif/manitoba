"""Manitoba Legacy Curriculum Scraper — FastAPI web application."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.science_scraper import scrape_all_science
from app.socstud_scraper import scrape_all_socstud
from app.math_scraper import scrape_all_math
from app.pehe_scraper import scrape_all_pehe
from app.cardev_scraper import scrape_all_cardev
from app.ela_scraper import scrape_all_ela, scrape_ela_legacy
from app.arts_scraper import scrape_all_arts
from app.sustour_scraper import scrape_all_sustour
from app.cs_scraper import scrape_all_cs
from app.ict_scraper import scrape_all_ict
from app.teched_scraper import scrape_all_teched
from app.arts912_scraper import scrape_all_arts912
from app.eal_framework_scraper import scrape_all_eal_framework
from app.eal_courses_scraper import scrape_all_eal_courses
from app.intl_lang_scraper import scrape_all_intl_languages
from app.indigenous_scraper import scrape_indigenous_gr12
from app.lwict_scraper import scrape_lwict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Manitoba Legacy Curriculum Scraper")

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

# Scraping state
scrape_state = {
    "is_running": False,
    "current_task": "",
    "log": [],
    "results": None,
}


def log_progress(message: str):
    """Add a progress message to the scrape log."""
    scrape_state["log"].append(message)
    scrape_state["current_task"] = message
    logger.info(message)


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Manitoba Legacy Curriculum Scraper</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #333; }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        h1 { text-align: center; margin-bottom: 10px; color: #1a5276; }
        .subtitle { text-align: center; color: #666; margin-bottom: 30px; font-size: 14px; }
        .card { background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); padding: 20px; margin-bottom: 20px; }
        .card h2 { margin-bottom: 15px; color: #2c3e50; font-size: 18px; }
        .btn-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
        button {
            padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer;
            font-size: 14px; font-weight: 600; color: white; transition: all 0.2s;
        }
        button:hover { opacity: 0.9; transform: translateY(-1px); }
        button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .btn-science { background: #27ae60; }
        .btn-social { background: #2980b9; }
        .btn-math { background: #8e44ad; }
        .btn-ela { background: #d35400; }
        .btn-physed { background: #16a085; }
        .btn-arts { background: #c0392b; }
        .btn-cardev { background: #e67e22; }
        .btn-sustour { background: #1abc9c; }
        .btn-cs { background: #3498db; }
        .btn-ict { background: #9b59b6; }
        .btn-teched { background: #e74c3c; }
        .btn-all { background: #2c3e50; }
        #log {
            background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 6px;
            font-family: 'Consolas', 'Monaco', monospace; font-size: 13px;
            max-height: 400px; overflow-y: auto; white-space: pre-wrap; line-height: 1.5;
            min-height: 100px;
        }
        .status { padding: 8px 12px; border-radius: 4px; margin-bottom: 15px; font-size: 14px; }
        .status-idle { background: #eee; color: #666; }
        .status-running { background: #fff3cd; color: #856404; }
        .status-done { background: #d4edda; color: #155724; }
        .status-error { background: #f8d7da; color: #721c24; }
        .results-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px; margin-top: 15px; }
        .result-item { background: #f8f9fa; padding: 10px; border-radius: 4px; border-left: 3px solid #27ae60; font-size: 13px; }
        .result-item.error { border-left-color: #e74c3c; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Manitoba Legacy Curriculum Scraper</h1>
        <p class="subtitle">Scrapes curriculum data from edu.gov.mb.ca (Legacy/Old Framework)</p>

        <div class="card">
            <h2>Scrape Subjects</h2>
            <div class="btn-row">
                <button class="btn-science" onclick="startScrape('science')" id="btn-science">Scrape Science (K-11)</button>
                <button class="btn-social" onclick="startScrape('social_studies')" id="btn-social">Scrape Social Studies</button>
                <button class="btn-math" onclick="startScrape('math')" id="btn-math">Scrape Mathematics</button>
            </div>
            <div class="btn-row">
                <button class="btn-ela" onclick="startScrape('ela')" id="btn-ela">Scrape ELA K-12 (New Framework)</button>
                <button class="btn-ela" onclick="startScrape('ela_legacy')" id="btn-ela-legacy" style="background:#a04000">Scrape ELA S1-S4 (Legacy)</button>
                <button class="btn-cardev" onclick="startScrape('cardev')" id="btn-cardev">Scrape Career Development</button>
                <button class="btn-physed" onclick="startScrape('physed')" id="btn-physed">Scrape Phys Ed / Health Ed</button>
                <button class="btn-arts" onclick="startScrape('arts')" id="btn-arts">Scrape Arts Education (K-8, per grade)</button>
            </div>
            <div class="btn-row">
                <button class="btn-sustour" onclick="startScrape('sustour')" id="btn-sustour">Scrape Sustainable Tourism (11-12)</button>
                <button class="btn-cs" onclick="startScrape('cs')" id="btn-cs">Scrape Computer Science (10-12)</button>
                <button class="btn-ict" onclick="startScrape('ict')" id="btn-ict">Scrape ICT Senior Years (15 courses)</button>
                <button class="btn-teched" onclick="startScrape('teched')" id="btn-teched">Scrape Tech Ed ACE (9-12)</button>
                <button class="btn-arts" onclick="startScrape('arts912')" id="btn-arts912" style="background:#922b21">Scrape Arts Education (9-12)</button>
                <button class="btn-ict" onclick="startScrape('eal_framework')" id="btn-eal-framework" style="background:#2ecc71">Scrape EAL Framework (K-12)</button>
                <button class="btn-ict" onclick="startScrape('eal_courses')" id="btn-eal-courses" style="background:#27ae60">Scrape EAL/LAL Courses (SY)</button>
                <button class="btn-ict" onclick="startScrape('intl_lang')" id="btn-intl-lang" style="background:#e67e22">Scrape International Languages (ASL/Spanish/Hebrew/German/Ukrainian)</button>
                <button class="btn-ict" onclick="startScrape('indigenous')" id="btn-indigenous" style="background:#8e44ad">Scrape Indigenous Education (Gr 12)</button>
                <button class="btn-ict" onclick="startScrape('lwict')" id="btn-lwict" style="background:#3498db">Scrape Literacy with ICT (K-12)</button>
            </div>
        </div>

        <div class="card">
            <h2>Status</h2>
            <div id="status" class="status status-idle">Idle — ready to scrape</div>
            <div id="log">Waiting for scrape to start...</div>
        </div>

        <div class="card">
            <h2>Results</h2>
            <div id="results">No results yet. Start a scrape above.</div>
        </div>
    </div>

    <script>
        let pollInterval = null;
        const allBtns = ['btn-science', 'btn-social', 'btn-math', 'btn-ela', 'btn-ela-legacy', 'btn-physed', 'btn-cardev', 'btn-arts', 'btn-sustour', 'btn-cs', 'btn-ict', 'btn-teched', 'btn-arts912', 'btn-eal-framework', 'btn-eal-courses', 'btn-intl-lang', 'btn-indigenous', 'btn-lwict'];
        const btnOrigText = {};

        function disableAll() {
            allBtns.forEach(id => {
                const btn = document.getElementById(id);
                if (btn && !btnOrigText[id]) btnOrigText[id] = btn.textContent;
                if (btn) btn.disabled = true;
            });
        }
        function enableAll() {
            allBtns.forEach(id => {
                const btn = document.getElementById(id);
                if (btn && btnOrigText[id]) btn.textContent = btnOrigText[id];
                // Only enable buttons that are implemented
                if (['btn-science', 'btn-social', 'btn-math', 'btn-physed', 'btn-cardev', 'btn-ela', 'btn-ela-legacy', 'btn-arts', 'btn-sustour', 'btn-cs', 'btn-ict', 'btn-teched', 'btn-arts912', 'btn-eal-framework', 'btn-eal-courses', 'btn-intl-lang', 'btn-indigenous', 'btn-lwict'].includes(id) && btn) btn.disabled = false;
            });
        }

        async function startScrape(subject) {
            disableAll();
            const btn = document.getElementById('btn-' + subject.replace('_', '-').replace('social-studies', 'social'));
            if (btn) btn.textContent = 'Scraping...';

            document.getElementById('status').className = 'status status-running';
            document.getElementById('status').textContent = 'Scraping in progress...';
            document.getElementById('log').textContent = 'Starting scrape...\\n';

            try {
                const resp = await fetch('/api/scrape/' + subject, { method: 'POST' });
                if (!resp.ok) {
                    const err = await resp.json();
                    throw new Error(err.detail || 'Scrape failed');
                }
            } catch (e) {
                document.getElementById('status').className = 'status status-error';
                document.getElementById('status').textContent = 'Error: ' + e.message;
                enableAll();
                return;
            }

            pollInterval = setInterval(pollStatus, 1500);
        }

        async function pollStatus() {
            try {
                const resp = await fetch('/api/status');
                const data = await resp.json();

                document.getElementById('log').textContent = data.log.join('\\n');
                const logDiv = document.getElementById('log');
                logDiv.scrollTop = logDiv.scrollHeight;

                if (!data.is_running) {
                    clearInterval(pollInterval);
                    pollInterval = null;
                    document.getElementById('status').className = 'status status-done';
                    document.getElementById('status').textContent = 'Scrape complete!';
                    enableAll();
                    loadResults();
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        }

        async function loadResults() {
            try {
                const resp = await fetch('/api/results');
                const data = await resp.json();
                const container = document.getElementById('results');

                if (data.files.length === 0) {
                    container.innerHTML = 'No results yet.';
                    return;
                }

                let html = '<div class="results-grid">';
                for (const f of data.files) {
                    html += `<div class="result-item">
                        <strong>${f.filename}</strong><br>
                        ${f.grade ? 'Grade: ' + f.grade + '<br>' : ''}
                        ${f.clusters !== undefined ? f.clusters + ' clusters, ' + f.slos + ' SLOs' : ''}
                    </div>`;
                }
                html += '</div>';
                container.innerHTML = html;
            } catch (e) {
                console.error('Results error:', e);
            }
        }

        // Load results on page load
        loadResults();
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.post("/api/scrape/{subject}")
async def start_scrape(subject: str):
    if scrape_state["is_running"]:
        return JSONResponse(
            {"detail": "A scrape is already in progress"},
            status_code=409,
        )

    scrape_state["is_running"] = True
    scrape_state["log"] = []
    scrape_state["results"] = None

    scrape_funcs = {
        "science": _run_science_scrape,
        "social_studies": _run_socstud_scrape,
        "math": _run_math_scrape,
        "physed": _run_pehe_scrape,
        "cardev": _run_cardev_scrape,
        "ela": _run_ela_scrape,
        "ela_legacy": _run_ela_legacy_scrape,
        "arts": _run_arts_scrape,
        "sustour": _run_sustour_scrape,
        "cs": _run_cs_scrape,
        "ict": _run_ict_scrape,
        "teched": _run_teched_scrape,
        "arts912": _run_arts912_scrape,
        "eal_framework": _run_eal_framework_scrape,
        "eal_courses": _run_eal_courses_scrape,
        "intl_lang": _run_intl_lang_scrape,
        "indigenous": _run_indigenous_scrape,
        "lwict": _run_lwict_scrape,
    }

    func = scrape_funcs.get(subject)
    if not func:
        scrape_state["is_running"] = False
        return JSONResponse(
            {"detail": f"Subject '{subject}' scraping not yet implemented"},
            status_code=400,
        )

    asyncio.get_event_loop().run_in_executor(None, func)

    return {"status": "started", "subject": subject}


def _subject_dir(name: str) -> Path:
    """Create and return a per-subject output directory."""
    d = OUTPUT_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_science_scrape():
    try:
        results = scrape_all_science(_subject_dir("Science"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Science scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_socstud_scrape():
    try:
        results = scrape_all_socstud(_subject_dir("SocialStudies"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Social Studies scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_math_scrape():
    try:
        results = scrape_all_math(_subject_dir("Mathematics"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Mathematics scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_pehe_scrape():
    try:
        results = scrape_all_pehe(_subject_dir("PhysEdHealthEd"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("PE/HE scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_arts_scrape():
    try:
        results = scrape_all_arts(_subject_dir("ArtsEducation"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Arts scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_ela_scrape():
    try:
        results = scrape_all_ela(_subject_dir("ELA"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("ELA scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_ela_legacy_scrape():
    try:
        results = scrape_ela_legacy(_subject_dir("ELA_Legacy"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("ELA Legacy scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_cardev_scrape():
    try:
        results = scrape_all_cardev(_subject_dir("CareerDevelopment"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Career Dev scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_sustour_scrape():
    try:
        results = scrape_all_sustour(_subject_dir("SustainableTourism"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Sustainable Tourism scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_cs_scrape():
    try:
        results = scrape_all_cs(_subject_dir("ComputerScience"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Computer Science scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_ict_scrape():
    try:
        results = scrape_all_ict(_subject_dir("ICT"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("ICT scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_teched_scrape():
    try:
        results = scrape_all_teched(_subject_dir("TechEdACE"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Tech Ed scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_arts912_scrape():
    try:
        results = scrape_all_arts912(_subject_dir("ArtsEducation_9-12"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Arts 9-12 scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_eal_framework_scrape():
    try:
        results = scrape_all_eal_framework(_subject_dir("EAL_Framework"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("EAL Framework scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_eal_courses_scrape():
    try:
        results = scrape_all_eal_courses(_subject_dir("EAL_Courses"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("EAL Courses scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_intl_lang_scrape():
    try:
        results = scrape_all_intl_languages(_subject_dir("Intl_Languages"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("International Languages scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_indigenous_scrape():
    try:
        results = scrape_indigenous_gr12(_subject_dir("Indigenous_Education"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("Indigenous Education scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


def _run_lwict_scrape():
    try:
        results = scrape_lwict(_subject_dir("LwICT"), progress_callback=log_progress)
        scrape_state["results"] = results
        log_progress("\nScrape complete!")
    except Exception as e:
        logger.exception("LwICT scrape failed")
        log_progress(f"\nFATAL ERROR: {e}")
    finally:
        scrape_state["is_running"] = False


@app.get("/api/status")
async def get_status():
    return {
        "is_running": scrape_state["is_running"],
        "current_task": scrape_state["current_task"],
        "log": scrape_state["log"],
        "results": scrape_state["results"],
    }


@app.get("/api/results")
async def get_results():
    files = []
    for filepath in sorted(OUTPUT_DIR.rglob("*.json")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            grade = data.get("grade", "")
            clusters = data.get("clusters", [])
            total_slos = sum(
                len(c.get("specific_learning_outcomes", []))
                for c in clusters
            )
            rel_path = filepath.relative_to(OUTPUT_DIR)
            files.append({
                "filename": str(rel_path),
                "grade": grade,
                "clusters": len(clusters),
                "slos": total_slos,
            })
        except Exception:
            rel_path = filepath.relative_to(OUTPUT_DIR)
            files.append({
                "filename": str(rel_path),
                "grade": "?",
                "clusters": 0,
                "slos": 0,
            })

    return {"files": files}
