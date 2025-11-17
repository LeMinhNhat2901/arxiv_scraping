import argparse
import json
import logging
import re
import tarfile
import time
import sys
import os
import psutil
import tracemalloc
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import lru_cache

import arxiv
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm
from bs4 import BeautifulSoup

# ----------------------------
# Configurable constants
# ----------------------------
ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{}"
ARXIV_ABS_URL = "https://arxiv.org/abs/{}"
SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1/paper/arXiv:{}"

DEFAULT_MAX_PAPER_WORKERS = 12
DEFAULT_SS_POOLSIZE = 20
DEFAULT_SESSION_TIMEOUT = 15
MAX_VERSION_TO_CHECK = 8
VERSION_HEAD_THREADS = 6
DEFAULT_DOWNLOAD_WORKERS = 4
SS_REQUESTS_PER_SECOND = 2
SS_REQUESTS_PER_5_MIN = 200

DEFAULT_STUDENT_ID = "23120067"
DEFAULT_ARXIV_RANGE = ["2504.13946", "2504.15000"]

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("scraper_optimized_report.log")],
)
logger = logging.getLogger(__name__)

# ----------------------------
# Utilities
# ----------------------------
def format_yymm_id(arxiv_id: str) -> str:
    arxiv_id = arxiv_id.replace("arXiv:", "").strip()
    arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
    if "." in arxiv_id:
        prefix, num = arxiv_id.split(".", 1)
        return f"{prefix}-{num}"
    return arxiv_id

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def _dir_size_bytes(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
    except Exception:
        return 0

def download_eprint_session(session: requests.Session, id_with_version: str, dest: Path, timeout: int = DEFAULT_SESSION_TIMEOUT) -> Optional[Path]:
    url = ARXIV_EPRINT_URL.format(id_with_version)
    try:
        with session.get(url, stream=True, timeout=timeout) as response:
            if response.status_code == 200:
                ensure_dir(dest.parent)
                with open(dest, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                return dest
            else:
                logger.debug(f"Download status {response.status_code} for {id_with_version}")
                return None
    except Exception as e:
        logger.debug(f"Download exception for {id_with_version}: {e}")
        return None

def remove_non_tex_bib(directory: Path) -> int:
    removed = 0
    keep_extensions = {'.tex', '.bib'}
    for p in directory.rglob('*'):
        if p.is_file() and p.suffix.lower() not in keep_extensions:
            try:
                p.unlink()
                removed += 1
            except Exception as e:
                logger.debug(f"Could not remove {p}: {e}")
    return removed

def flatten_directory(directory: Path):
    items = list(directory.iterdir())
    if len(items) == 1 and items[0].is_dir():
        subdir = items[0]
        for item in subdir.iterdir():
            try:
                item.rename(directory / item.name)
            except Exception as e:
                logger.debug(f"Could not move {item}: {e}")
        try:
            subdir.rmdir()
        except Exception as e:
            logger.debug(f"Could not remove subdirectory {subdir}: {e}")

# ----------------------------
# Lightweight rate limiter
# ----------------------------
class RateLimiter:
    def __init__(self, per_second=SS_REQUESTS_PER_SECOND, per_5_min=SS_REQUESTS_PER_5_MIN):
        self.per_second = per_second
        self.per_5_min = per_5_min
        self.window = 300.0
        self.times = deque()
        self.last_second = 0.0

    def wait_if_needed(self):
        now = time.time()
        if now - self.last_second < 1.0 / max(1, self.per_second):
            time.sleep(max(0.0, (1.0 / max(1, self.per_second)) - (now - self.last_second)))
        self.last_second = time.time()
        now = time.time()
        while self.times and self.times[0] < now - self.window:
            self.times.popleft()
        if len(self.times) >= self.per_5_min:
            wait = self.times[0] + self.window - now
            logger.warning(f"Rate limit window full, sleeping {wait:.1f}s")
            time.sleep(wait)
        self.times.append(time.time())

# ----------------------------
# Scraper
# ----------------------------
class ArxivScraper:
    def __init__(self, student_id: str, output_dir: Optional[str] = None,
                 max_workers: int = DEFAULT_MAX_PAPER_WORKERS,
                 download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
                 ss_poolsize: int = DEFAULT_SS_POOLSIZE,
                 session_timeout: int = DEFAULT_SESSION_TIMEOUT):
        self.student_id = student_id
        self.output_dir = Path(output_dir) if output_dir else Path(student_id)
        ensure_dir(self.output_dir)
        self.max_workers = max_workers
        self.download_workers = download_workers
        self.session_timeout = session_timeout

        # Requests session + adapter
        self.session = requests.Session()
        retries = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=ss_poolsize, pool_maxsize=ss_poolsize, max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.ss_rate_limiter = RateLimiter()

        # memory tracking
        tracemalloc.start()
        self.process = psutil.Process()

        # Stats initialization (expanded)
        self.stats = {
            'papers_attempted': 0,
            'papers_successful': 0,
            'papers_failed': 0,
            'total_references': 0,
            'references_found': 0,
            'references_with_arxiv': 0,
            'references_per_paper': [],
            'ss_requests_made': 0,
            'ss_rate_limit_hits': 0,
            'ss_errors': 0,
            'versions_found': 0,
            'versions_downloaded': 0,
            'version_checks': 0,
            'files_removed': 0,
            'start_time': time.time(),
            'paper_processing_times': [],
            'entry_discovery_time': 0.0,
            'reference_scraping_time': 0.0,
            'download_time': 0.0,
            'paper_sizes_before_bytes': [],
            'paper_sizes_after_bytes': [],
            'memory_samples_mb': [],
            'peak_memory_mb': 0.0,
            'disk_usage_samples_bytes': [],
        }

    @lru_cache(maxsize=1024)
    def _ss_get_cached(self, arxiv_id: str, fields: str) -> Optional[Dict]:
        self.ss_rate_limiter.wait_if_needed()
        self.stats['ss_requests_made'] += 1
        url = SEMANTIC_SCHOLAR_BASE.format(arxiv_id)
        try:
            resp = self.session.get(url, params={'fields': fields}, timeout=self.session_timeout)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                self.stats['ss_rate_limit_hits'] += 1
                logger.warning(f"SS rate limited for {arxiv_id}")
                return None
            elif resp.status_code == 404:
                return {}
            else:
                return {}
        except Exception as e:
            self.stats['ss_errors'] += 1
            logger.debug(f"SS request error for {arxiv_id}: {e}")
            return None

    def _fetch_references_and_paper_venue(self, arxiv_id: str) -> Tuple[Dict[str, Dict], Optional[str]]:
        t0 = time.time()
        references = {}
        paper_venue = None
        fields = ("paperId,venue,publicationVenue,"
                "references,references.externalIds,references.title,references.authors,"
                "references.publicationDate,references.paperId,references.venue")
        data = self._ss_get_cached(arxiv_id, fields)
        if data is None:
            self.stats['reference_scraping_time'] += (time.time() - t0)  
            return {}, None
        if not data:
            self.stats['reference_scraping_time'] += (time.time() - t0)  
            return {}, None

        pubv = data.get('publicationVenue')
        if isinstance(pubv, dict):
            nm = pubv.get('name')
            if nm and nm.strip():
                paper_venue = nm.strip()
        if not paper_venue:
            vs = data.get('venue')
            if isinstance(vs, str) and vs.strip():
                paper_venue = vs.strip()

        refs = data.get('references', [])
        self.stats['references_found'] += len(refs)
        arxiv_refs_count = 0
        for i, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            arxiv_ref_id = self._extract_arxiv_id_comprehensive(ref, i)
            if arxiv_ref_id and self._is_valid_arxiv_id(arxiv_ref_id):
                arxiv_refs_count += 1
                formatted_ref_id = format_yymm_id(arxiv_ref_id)
                ref_data = self._build_reference_metadata_accurate(ref, arxiv_ref_id)
                if ref_data:
                    references[formatted_ref_id] = ref_data
        self.stats['references_with_arxiv'] += arxiv_refs_count
        self.stats['reference_scraping_time'] += (time.time() - t0) 
        return references, paper_venue

    def _build_reference_metadata_accurate(self, ref: Dict, arxiv_ref_id: str) -> Optional[Dict]:
        try:
            authors = []
            for author in ref.get('authors', []) or []:
                if isinstance(author, dict):
                    name = author.get('name', '')
                    if name and name.strip():
                        authors.append(name.strip())
            submission_date = ''
            pub_date = ref.get('publicationDate', '') or ''
            if pub_date and pub_date.strip():
                submission_date = pub_date.strip()
            if not submission_date:
                submission_date = self._get_submission_date_from_arxiv(arxiv_ref_id)
            if not submission_date:
                year = ref.get('year')
                try:
                    year_int = int(year)
                    if 1990 <= year_int <= 2030:
                        submission_date = f"{year_int}-01-01"
                except Exception:
                    pass
            metadata = {
                'paper_title': ref.get('title', ''),
                'authors': authors,
                'submission_date': submission_date,
            }
            pid = ref.get('paperId')
            if pid:
                metadata['semantic_scholar_id'] = pid
            if not metadata['paper_title']:
                return None
            return metadata
        except Exception as e:
            logger.debug(f"Error building ref metadata: {e}")
            return None

    def _get_submission_date_from_arxiv(self, arxiv_id: str) -> str:
        try:
            base_id = re.sub(r'v\d+$', '', arxiv_id)
            url = ARXIV_ABS_URL.format(base_id)
            r = self.session.get(url, timeout=8)
            if r.status_code != 200:
                return ""
            soup = BeautifulSoup(r.text, 'html.parser')
            month_map = {'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06',
                         'Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12'}
            patterns = [
                r'Submitted on (\d{1,2}) (\w{3}) (\d{4})',
                r'\[Submitted on (\d{1,2}) (\w{3}) (\d{4})\]',
                r'v1.*?(\d{1,2})\s+(\w{3})\s+(\d{4})',
            ]
            page_text = soup.get_text()
            for pattern in patterns:
                m = re.search(pattern, page_text)
                if m:
                    day, month_name, year = m.groups()
                    month = month_map.get(month_name, '01')
                    return f"{year}-{month}-{day.zfill(2)}"
            return ""
        except Exception as e:
            logger.debug(f"Error quick scraping arXiv date: {e}")
            return ""

    def _extract_arxiv_id_comprehensive(self, ref: Dict, ref_index: int) -> Optional[str]:
        if not isinstance(ref, dict):
            return None
        ext_ids = ref.get('externalIds', {})
        if isinstance(ext_ids, dict):
            for key in ('ArXiv','arXiv','arxiv','ARXIV'):
                v = ext_ids.get(key)
                if v:
                    return v
        url = ref.get('url','') or ''
        if url and 'arxiv.org' in url:
            patterns = [r'arxiv\.org/abs/([\d\.]+v?\d*)', r'arxiv\.org/pdf/([\d\.]+v?\d*)']
            for p in patterns:
                m = re.search(p, url, re.IGNORECASE)
                if m:
                    return m.group(1)
        return None

    def _is_valid_arxiv_id(self, arxiv_id: str) -> bool:
        if not arxiv_id:
            return False
        base = re.sub(r'v\d+$','',arxiv_id)
        pattern = r'^(\w+\.)?\d{2}(0[1-9]|1[0-2])\.\d{4,5}$'
        return bool(re.match(pattern, base))

    def _fetch_metadata_with_complete_versions(self, arxiv_id: str, venue_override: Optional[str]=None) -> Tuple[Optional[Dict], List[Dict]]:
        t0 = time.time()
        try:
            search = arxiv.Search(id_list=[arxiv_id])
            paper = next(search.results())
            all_versions = self._discover_all_versions(arxiv_id)
            if not all_versions:
                all_versions = [1]
            self.stats['versions_found'] += len(all_versions)
            versions_info = [{'version': v, 'id': f"{arxiv_id}v{v}"} for v in all_versions]
            metadata = {
                'paper_title': paper.title,
                'authors': [a.name for a in paper.authors],
                'submission_date': paper.published.strftime('%Y-%m-%d') if hasattr(paper, 'published') else '',
            }
            metadata['publication_venue'] = ""
            if venue_override:
                metadata['publication_venue'] = venue_override
            else:
                data = self._ss_get_cached(arxiv_id, 'venue,publicationVenue')
                if data:
                    pubv = data.get('publicationVenue')
                    if isinstance(pubv, dict):
                        nm = pubv.get('name')
                        if nm and nm.strip():
                            metadata['publication_venue'] = nm.strip()
                    elif isinstance(data.get('venue'), str) and data.get('venue').strip():
                        metadata['publication_venue'] = data.get('venue').strip()
                if metadata['publication_venue'] == "" and hasattr(paper, 'journal_ref') and paper.journal_ref:
                    metadata['publication_venue'] = paper.journal_ref.strip()
            revised_dates = self._scrape_version_dates_light(arxiv_id)
            if revised_dates:
                metadata['revised_dates'] = revised_dates
            self.stats['entry_discovery_time'] += (time.time() - t0)
            return metadata, versions_info
        except StopIteration:
            logger.error(f"No arXiv entry for {arxiv_id}")
            return None, []
        except Exception as e:
            logger.exception(f"Metadata fetch failed for {arxiv_id}: {e}")
            return None, []

    def _scrape_version_dates_light(self, arxiv_id: str) -> List[str]:
        try:
            url = ARXIV_ABS_URL.format(arxiv_id)
            r = self.session.get(url, timeout=8)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, 'html.parser')
            text = soup.get_text()
            month_map = {'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06',
                         'Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12'}
            pattern = r'\[v(\d+)\].*?(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})'
            found = []
            for m in re.finditer(pattern, text):
                vnum, day, month_name, year = m.groups()
                month = month_map.get(month_name,'01')
                date_str = f"{year}-{month}-{day.zfill(2)}"
                if date_str not in found:
                    found.append(date_str)
            found.sort()
            return found
        except Exception as e:
            logger.debug(f"Light scrape failed: {e}")
            return []

    def _check_version_exists(self, session: requests.Session, arxiv_id_with_version: str) -> bool:
        url = ARXIV_EPRINT_URL.format(arxiv_id_with_version)
        try:
            resp = session.head(url, timeout=6, allow_redirects=True)
            self.stats['version_checks'] += 1
            return resp.status_code == 200
        except Exception:
            return False

    def _discover_all_versions(self, arxiv_id: str) -> List[int]:
        max_v = min(MAX_VERSION_TO_CHECK, 20)
        with ThreadPoolExecutor(max_workers=VERSION_HEAD_THREADS) as ex:
            futures = {ex.submit(self._check_version_exists, self.session, f"{arxiv_id}v{v}"): v for v in range(1, max_v+1)}
            results = []
            for fut in as_completed(futures):
                v = futures[fut]
                try:
                    exists = fut.result()
                    if exists:
                        results.append(v)
                except Exception:
                    pass
            results.sort()
            if results:
                contiguous = []
                for v in range(1, max(results)+1):
                    if v in results:
                        contiguous.append(v)
                    else:
                        break
                if contiguous:
                    return contiguous
            if self._check_version_exists(self.session, f"{arxiv_id}v1"):
                return [1]
            return []

    def _download_versions_concurrently(self, formatted: str, base_dot_id: str, versions_info: List[Dict]) -> int:
        tex_root = self.output_dir / formatted / 'tex'
        ensure_dir(tex_root)
        successful_versions = 0
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=min(self.download_workers, max(1, len(versions_info)))) as executor:
            future_to_vi = {}
            for v in versions_info:
                version_num = v['version']
                version_id = v['id']
                version_folder = f"{formatted}v{version_num}"
                version_dir = tex_root / version_folder
                fut = executor.submit(self._download_and_process_version, version_id, version_dir, tex_root, version_folder)
                future_to_vi[fut] = version_id
            for fut in as_completed(future_to_vi):
                try:
                    ok = fut.result()
                    if ok:
                        successful_versions += 1
                        self.stats['versions_downloaded'] += 1
                except Exception as e:
                    logger.debug(f"Download thread error: {e}")
        self.stats['download_time'] += (time.time() - t0)
        return successful_versions

    def _download_and_process_version(self, version_id: str, version_dir: Path, tex_root: Path, version_folder: str) -> bool:
        ensure_dir(version_dir)
        tar_path = tex_root / f"{version_folder}.tar.gz"
        saved = download_eprint_session(self.session, version_id, tar_path)
        if saved and saved.exists():
            try:
                with tarfile.open(saved, 'r:gz') as tar:
                    tar.extractall(path=version_dir)
                flatten_directory(version_dir)
                removed = remove_non_tex_bib(version_dir)
                self.stats['files_removed'] += removed
                return True
            except Exception as e:
                logger.debug(f"Extraction failed for {version_id}: {e}")
                return False
            finally:
                try:
                    saved.unlink()
                except Exception:
                    pass
        else:
            return False

    def scrape_single_paper(self, arxiv_id: str):
        logger.info(f"Start processing {arxiv_id}")
        start_time = time.time()
        base_dot_id = arxiv_id.replace('arXiv:', '').strip()
        base_dot_id = re.sub(r"v\d+$", '', base_dot_id)
        formatted = format_yymm_id(base_dot_id)
        paper_dir = self.output_dir / formatted
        ensure_dir(paper_dir)

        # size before (if dir exists)
        size_before = _dir_size_bytes(paper_dir)
        self.stats['paper_sizes_before_bytes'].append(size_before)

        # parallel fetch refs and basic metadata
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_refs = ex.submit(self._fetch_references_and_paper_venue, base_dot_id)
            fut_meta = ex.submit(self._fetch_metadata_with_complete_versions, base_dot_id, None)

            try:
                references, venue_from_refs = fut_refs.result(timeout=30)
            except Exception as e:
                logger.debug(f"References fetch error for {base_dot_id}: {e}")
                references, venue_from_refs = {}, None

            self.stats['total_references'] += len(references)
            self.stats['references_per_paper'].append(len(references))

            try:
                meta_result, versions_info = fut_meta.result(timeout=30)
            except Exception as e:
                logger.debug(f"Metadata future error for {base_dot_id}: {e}")
                meta_result, versions_info = None, []

            if meta_result is None:
                meta_result, versions_info = self._fetch_metadata_with_complete_versions(base_dot_id, venue_override=venue_from_refs)

            if venue_from_refs:
                meta_result['publication_venue'] = venue_from_refs
            else:
                if 'publication_venue' not in meta_result or not isinstance(meta_result.get('publication_venue'), str):
                    meta_result['publication_venue'] = ""

            if not meta_result:
                logger.error(f"No metadata for {base_dot_id}, skipping")
                raise Exception(f"No metadata for {base_dot_id}")

            # Save metadata & references ASAP
            self._save_json(paper_dir / 'metadata.json', meta_result)
            self._save_json(paper_dir / 'references.json', references)

            # parallel downloads
            successful_versions = self._download_versions_concurrently(formatted, base_dot_id, versions_info)

        # size after cleaning
        size_after = _dir_size_bytes(paper_dir)
        self.stats['paper_sizes_after_bytes'].append(size_after)

        # memory sample
        try:
            mem = self.process.memory_info().rss / 1024 / 1024
            self.stats['memory_samples_mb'].append(mem)
            self.stats['peak_memory_mb'] = max(self.stats.get('peak_memory_mb', 0.0), mem)
            # disk usage sample (output dir)
            disk_bytes = _dir_size_bytes(self.output_dir)
            self.stats['disk_usage_samples_bytes'].append(disk_bytes)
        except Exception:
            pass

        elapsed = time.time() - start_time
        self.stats['paper_processing_times'].append(elapsed)
        logger.info(f"Finished {formatted} in {elapsed:.2f}s (versions downloaded: {successful_versions})")
        return True

    def scrape_papers(self, arxiv_ids: List[str]):
        logger.info(f"Scraping {len(arxiv_ids)} papers with max_workers={self.max_workers}")
        start = time.time()
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(arxiv_ids)))) as ex:
            future_to_id = {ex.submit(self._scrape_single_paper_safe, aid): aid for aid in arxiv_ids}
            with tqdm(total=len(arxiv_ids), desc="Papers") as pbar:
                for fut in as_completed(future_to_id):
                    aid = future_to_id[fut]
                    self.stats['papers_attempted'] += 1
                    try:
                        ok = fut.result()
                        if ok:
                            self.stats['papers_successful'] += 1
                        else:
                            self.stats['papers_failed'] += 1
                    except Exception as e:
                        self.stats['papers_failed'] += 1
                        logger.exception(f"Error scraping {aid}: {e}")
                    pbar.update(1)
        total_elapsed = time.time() - start
        logger.info(f"All done in {total_elapsed:.2f}s")
        self._print_stats()
        self._generate_report()

    def _scrape_single_paper_safe(self, arxiv_id: str) -> bool:
        try:
            return self.scrape_single_paper(arxiv_id)
        except Exception as e:
            logger.error(f"Failed {arxiv_id}: {e}")
            return False

    def _save_json(self, path: Path, obj: Dict):
        try:
            ensure_dir(path.parent)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(obj, f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved {path}")
        except Exception as e:
            logger.exception(f"Failed to save {path}: {e}")

    # ----------------------------
    # Final report generator (matches your requested items)
    # ----------------------------
    def _generate_report(self):
        elapsed = time.time() - self.stats['start_time']
        attempted = self.stats['papers_attempted']
        success = self.stats['papers_successful']
        overall_success_rate = (100.0 * success / attempted) if attempted else 0.0
        #avg_time_per_paper = (sum(self.stats['paper_processing_times']) / len(self.stats['paper_processing_times'])) if self.stats['paper_processing_times'] else 0.0
        avg_time_per_paper = elapsed / success if success > 0 else 0.0
        avg_sequential_time = (sum(self.stats['paper_processing_times']) / len(self.stats['paper_processing_times'])) if self.stats['paper_processing_times'] else 0.0

        # sizes
        avg_size_before = (sum(self.stats['paper_sizes_before_bytes']) / len(self.stats['paper_sizes_before_bytes'])) if self.stats['paper_sizes_before_bytes'] else 0
        avg_size_after = (sum(self.stats['paper_sizes_after_bytes']) / len(self.stats['paper_sizes_after_bytes'])) if self.stats['paper_sizes_after_bytes'] else 0

        # references
        avg_refs_per_paper = (sum(self.stats['references_per_paper']) / len(self.stats['references_per_paper'])) if self.stats['references_per_paper'] else 0.0
        ref_success_rate = (100.0 * self.stats['references_with_arxiv'] / self.stats['references_found']) if self.stats['references_found'] else 0.0

        # memory
        peak_mem = self.stats.get('peak_memory_mb', 0.0)
        avg_mem = (sum(self.stats['memory_samples_mb']) / len(self.stats['memory_samples_mb'])) if self.stats['memory_samples_mb'] else 0.0

        # disk usage
        max_disk_bytes = max(self.stats['disk_usage_samples_bytes']) if self.stats['disk_usage_samples_bytes'] else _dir_size_bytes(self.output_dir)
        final_output_size = _dir_size_bytes(self.output_dir)

        # Implementation brief (for submission docs)
        implementation_summary = (
            "This scraper collects arXiv papers' TeX sources and metadata. "
            "Workflow: for each arXiv id -> concurrently fetch Semantic Scholar references (one SS call which includes paper-level venue if available) "
            "and arXiv metadata (versions discovery via HEAD checks). Then download TeX sources for discovered versions, extract and keep .tex/.bib files. "
            "Tools: arxiv (python library), requests, BeautifulSoup4, concurrent.futures, Semantic Scholar REST API. "
            "Optimizations: requests.Session pooling, small LRU cache for SS calls, parallelism at paper-level and per-paper (HEAD checks & downloads), lightweight rate-limiter."
        )

        report = {
            "report_metadata": {
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "student_id": self.student_id,
                "total_runtime_seconds": round(elapsed, 2),
                "testbed": "Intended for Google Colab (CPU-only) per instructions; actual runtime depends on environment."
            },
            "implementation_summary": implementation_summary,
            "tools_and_libraries": [
                "python (3.x)",
                "arxiv (python package)",
                "requests",
                "BeautifulSoup4 (bs4)",
                "concurrent.futures (ThreadPoolExecutor)",
                "urllib3",
                "psutil",
                "tracemalloc",
                "Semantic Scholar API (graph/v1)"
            ],
            "scraping_statistics": {
                "papers_attempted": attempted,
                "papers_successful": success,
                "papers_failed": self.stats['papers_failed'],
                "overall_success_rate_percent": round(overall_success_rate, 2),
                "average_time_per_paper_seconds": round(avg_time_per_paper, 3),
                "average_sequential_processing_time_seconds": round(avg_sequential_time, 3),
                "entry_discovery_total_seconds": round(self.stats.get('entry_discovery_time', 0.0), 3),
                "reference_scraping_total_seconds": round(self.stats.get('reference_scraping_time', 0.0), 3),
                "download_total_seconds": round(self.stats.get('download_time', 0.0), 3)
            },
            "size_statistics_bytes": {
                "average_size_before_bytes": int(avg_size_before),
                "average_size_after_bytes": int(avg_size_after),
                "final_output_size_bytes": int(final_output_size),
                "max_disk_usage_sampled_bytes": int(max_disk_bytes)
            },
            "references_statistics": {
                "total_references_found": self.stats['references_found'],
                "references_with_arxiv_ids": self.stats['references_with_arxiv'],
                "average_references_per_paper": round(avg_refs_per_paper, 2),
                "reference_arxiv_id_extraction_rate_percent": round(ref_success_rate, 2)
            },
            "performance_memory": {
                "peak_memory_mb": round(peak_mem, 2),
                "average_memory_mb": round(avg_mem, 2)
            },
            "other_metrics": {
                "ss_requests_made": self.stats['ss_requests_made'],
                "ss_rate_limit_hits": self.stats['ss_rate_limit_hits'],
                "versions_found_total": self.stats['versions_found'],
                "versions_downloaded_total": self.stats['versions_downloaded']
            }
        }

        # Save report as performance_report_{student}.json
        report_path = self.output_dir / f"performance_report_{self.student_id}.json"
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            logger.info(f"Report saved to {report_path}")
        except Exception as e:
            logger.exception(f"Could not write report: {e}")

    def _print_stats(self):
        elapsed = time.time() - self.stats['start_time']
        attempted = self.stats['papers_attempted']
        success = self.stats['papers_successful']
        avg_time = elapsed / success if success > 0 else 0.0
        
        print('\n' + '='*40)
        print('SCRAPING STATISTICS SUMMARY')
        print('='*40)
        print(f"Papers: {success}/{attempted}")
        print(f"Avg time/paper: {avg_time:.2f}s")
        print(f"SS requests made: {self.stats.get('ss_requests_made',0)}")
        print(f"Peak mem (MB): {self.stats.get('peak_memory_mb',0.0):.2f}")
        print(f"Final output bytes: {_dir_size_bytes(self.output_dir)}")
        print('='*40)

# ----------------------------
# CLI helpers
# ----------------------------
def generate_arxiv_ids(start_id: str, end_id: str) -> List[str]:
    try:
        sp, ep = start_id.split('.'), end_id.split('.')
        s_num, e_num = int(sp[1]), int(ep[1])
        prefix = sp[0]
        return [f"{prefix}.{i:05d}" for i in range(s_num, e_num + 1)]
    except Exception as e:
        logger.error(f"Error generating IDs: {e}")
        return []

def is_colab_environment():
    return 'COLAB_GPU' in os.environ or 'google.colab' in sys.modules

# ----------------------------
# Main
# ----------------------------
def main():
    if is_colab_environment() or len(sys.argv) == 1:
        print("Running with default configuration (optimized + reporting)...")
        student_id = DEFAULT_STUDENT_ID
        arxiv_range = DEFAULT_ARXIV_RANGE
        ids = generate_arxiv_ids(arxiv_range[0], arxiv_range[1])
        output_dir = f"./{student_id}"
        scraper = ArxivScraper(
            student_id=student_id,
            output_dir=output_dir,
            max_workers=DEFAULT_MAX_PAPER_WORKERS,
            download_workers=DEFAULT_DOWNLOAD_WORKERS
        )
        scraper.scrape_papers(ids)
    else:
        parser = argparse.ArgumentParser(description='Optimized ArXiv scraper with report')
        parser.add_argument('--student', required=True, help='Student ID')
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--range', nargs=2, help='Start and end arXiv IDs')
        group.add_argument('--ids', nargs='+', help='List of arXiv IDs')
        parser.add_argument('--out', help='Output directory')
        parser.add_argument('--workers', type=int, default=DEFAULT_MAX_PAPER_WORKERS)
        parser.add_argument('--dworkers', type=int, default=DEFAULT_DOWNLOAD_WORKERS)
        args = parser.parse_args()

        if args.range:
            ids = generate_arxiv_ids(args.range[0], args.range[1])
        else:
            ids = args.ids

        scraper = ArxivScraper(
            student_id=args.student,
            output_dir=args.out,
            max_workers=args.workers,
            download_workers=args.dworkers
        )
        scraper.scrape_papers(ids)

if __name__ == '__main__':
    main()
