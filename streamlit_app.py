import streamlit as st
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from datetime import datetime

# ---------------------------
# Utilities
# ---------------------------

def guess_remote(text: str) -> bool:
    """Heuristic: mark remote if job/location text mentions remote or 'anywhere'."""
    if not text:
        return False
    t = text.lower()
    keywords = ["remote", "anywhere", "work from home", "distributed"]
    return any(k in t for k in keywords)

def filter_results(jobs, role_kw, loc_kw, extra_kw):
    """Apply basic keyword filters client-side."""
    role_kw = role_kw.strip().lower()
    loc_kw = loc_kw.strip().lower()
    extra_kw = extra_kw.strip().lower()

    filtered = []
    for job in jobs:
        blob = " ".join([
            job.get("title",""),
            job.get("company",""),
            job.get("location",""),
            job.get("source","")
        ]).lower()

        # role keyword must match title
        if role_kw and role_kw not in job.get("title","").lower():
            continue
        # location keyword must match location/remote text
        if loc_kw and loc_kw not in (job.get("location","").lower() + " " + job.get("title","").lower()):
            continue
        # extra keyword (industry / salary / clearance string) must match anywhere
        if extra_kw and extra_kw not in blob:
            continue

        filtered.append(job)

    return filtered


# ---------------------------
# Scraper: Lever
# ---------------------------

def scrape_lever(company_handle):
    """
    Pull jobs from Lever for a given company handle.
    Example handle: 'saic', 'anduril-industries', 'openai', etc.
    We'll try the public API-ish JSON if available, otherwise HTML fallback.
    """
    out = []

    # Lever usually exposes a JSON-style endpoint like:
    # https://jobs.lever.co/{company}?mode=json
    json_url = f"https://jobs.lever.co/{company_handle}?mode=json"

    try:
        resp = requests.get(json_url, timeout=10)
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
            for item in data:
                title = item.get("text", "")
                loc = item.get("additional", {}).get("location", "")
                lever_url = item.get("hostedUrl", "")
                # Lever doesn't always expose datePosted cleanly, so fallback to now
                date_posted = item.get("createdAt", "")
                if date_posted:
                    # createdAt is millis sometimes
                    try:
                        ts = int(date_posted) / 1000.0
                        date_posted = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                    except Exception:
                        pass

                out.append({
                    "company": company_handle,
                    "title": title,
                    "location": loc,
                    "remote": guess_remote(loc),
                    "source": "Lever",
                    "url": lever_url,
                    "date_posted": date_posted
                })
            return out
    except Exception:
        pass

    # HTML fallback if no JSON:
    html_url = f"https://jobs.lever.co/{company_handle}"
    try:
        resp = requests.get(html_url, timeout=10)
        if resp.status_code != 200:
            return out
        soup = BeautifulSoup(resp.text, "html.parser")
        postings = soup.select("div.posting")
        for p in postings:
            title_el = p.select_one("h5.posting-title")
            loc_el = p.select_one("span.sort-by-location")
            link_el = p.select_one("a.posting-title")

            title = title_el.get_text(strip=True) if title_el else ""
            loc = loc_el.get_text(strip=True) if loc_el else ""
            href = link_el["href"] if link_el and link_el.has_attr("href") else ""
            if href and not href.startswith("http"):
                href = f"https://jobs.lever.co{href}"

            out.append({
                "company": company_handle,
                "title": title,
                "location": loc,
                "remote": guess_remote(loc),
                "source": "Lever",
                "url": href,
                "date_posted": ""
            })
    except Exception:
        pass

    return out


# ---------------------------
# Scraper: Greenhouse
# ---------------------------

def scrape_greenhouse(board_token):
    """
    Pull jobs from Greenhouse for a given board token.
    Example token: 'andurilindustries', 'palantir', 'rivethealth' etc.
    Greenhouse exposes a nice JSON board API:
    https://boards-api.greenhouse.io/v1/boards/{token}/jobs
    """
    out = []
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    try:
        resp = requests.get(api_url, timeout=10)
        if resp.status_code != 200:
            return out
        data = resp.json()
        for job in data.get("jobs", []):
            title = job.get("title", "")
            locs = job.get("location", {}).get("name", "")
            gh_url = job.get("absolute_url", "")
            date_posted = job.get("updated_at", "")[:10]

            out.append({
                "company": board_token,
                "title": title,
                "location": locs,
                "remote": guess_remote(locs),
                "source": "Greenhouse",
                "url": gh_url,
                "date_posted": date_posted
            })
    except Exception:
        pass

    return out


# ---------------------------
# Fetch from multiple companies
# ---------------------------

def fetch_all_jobs(lever_companies, greenhouse_tokens):
    jobs = []
    for c in lever_companies:
        jobs.extend(scrape_lever(c))
    for g in greenhouse_tokens:
        jobs.extend(scrape_greenhouse(g))
    return jobs


# ---------------------------
# Streamlit App
# ---------------------------

def init_state():
    if "saved_jobs" not in st.session_state:
        st.session_state["saved_jobs"] = []

def save_job(job_row):
    # avoid duplicates by url
    urls = [j["url"] for j in st.session_state["saved_jobs"]]
    if job_row["url"] not in urls:
        st.session_state["saved_jobs"].append(job_row)

def main():
    st.set_page_config(
        page_title="Job Finder",
        layout="wide"
    )
    init_state()

    st.title("🔍 Job Finder MVP")
    st.write("Search Lever + Greenhouse job boards for senior roles, QA, SDET, AI, Defense, etc.")

    with st.form("search_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            role_kw = st.text_input("Role / Title must include", "Senior Software Test Engineer")
            extra_kw = st.text_input("Industry / extra keyword (AI, Defense, Clearance, etc.)", "Defense")
        with col2:
            loc_kw = st.text_input("Location keyword (Remote, San Jose, CA, etc.)", "Remote / United States")
            companies_input = st.text_area(
                "Companies / boards (one per line)",
                # These are examples you can edit. You can mix Lever handles and Greenhouse tokens.
                "saic (lever)\nandurilindustries (greenhouse)\nopenai (lever)\npalantir (greenhouse)"
            )

        submitted = st.form_submit_button("Search Jobs 🚀")

    all_jobs_df = pd.DataFrame()

    if submitted:
        # parse company lines
        lever_targets = []
        greenhouse_targets = []
        for raw in companies_input.strip().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            # crude parse: "name (lever)" or "name (greenhouse)"
            if raw.endswith("(lever)"):
                lever_targets.append(raw.replace("(lever)", "").strip())
            elif raw.endswith("(greenhouse)"):
                greenhouse_targets.append(raw.replace("(greenhouse)", "").strip())
            else:
                # if user forgets to annotate, just assume it's Lever first
                lever_targets.append(raw)

        jobs = fetch_all_jobs(lever_targets, greenhouse_targets)
        jobs_filtered = filter_results(jobs, role_kw, loc_kw, extra_kw)

        if jobs_filtered:
            all_jobs_df = pd.DataFrame(jobs_filtered)
            # nice sort: newest first if we have a date
            if "date_posted" in all_jobs_df.columns:
                all_jobs_df["date_posted"] = pd.to_datetime(all_jobs_df["date_posted"], errors="coerce")
                all_jobs_df = all_jobs_df.sort_values(by="date_posted", ascending=False)

            st.subheader(f"Found {len(all_jobs_df)} matching roles")
            st.caption("Click a row to copy the URL and apply fast.")

            st.dataframe(
                all_jobs_df[["title", "company", "location", "remote", "date_posted", "source", "url"]],
                use_container_width=True
            )

            # download button
            csv_data = all_jobs_df.to_csv(index=False)
            st.download_button(
                label="⬇️ Download results as CSV",
                data=csv_data,
                file_name="job_search_results.csv",
                mime="text/csv"
            )

            # save a specific job
            st.markdown("---")
            st.write("⭐ Save a job to your shortlist")
            if not all_jobs_df.empty:
                # pick by index
                options = list(range(len(all_jobs_df)))
                pick = st.selectbox("Select job index to save", options)
                if st.button("Save Selected ⭐"):
                    row = all_jobs_df.iloc[pick].to_dict()
                    save_job(row)
                    st.success("Saved.")

        else:
            st.warning("No matches. Try loosening filters (ex: remove 'Senior', or clear location).")

    # sidebar or bottom section: saved jobs
    st.markdown("## ⭐ Saved jobs this session")
    if st.session_state["saved_jobs"]:
        saved_df = pd.DataFrame(st.session_state["saved_jobs"])
        st.dataframe(
            saved_df[["title", "company", "location", "remote", "date_posted", "source", "url"]],
            use_container_width=True
        )
        saved_csv = saved_df.to_csv(index=False)
        st.download_button(
            label="⬇️ Download saved jobs CSV",
            data=saved_csv,
            file_name="saved_jobs.csv",
            mime="text/csv"
        )
    else:
        st.caption("No saved jobs yet.")


if __name__ == "__main__":
    main()