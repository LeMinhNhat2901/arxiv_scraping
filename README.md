# Data Science Project - Milestone 1

*ArXiv Data Scraping and Repository Engineering*

---

## 🧭 Overview

- This project is part of the **Introduction to Data Science** course offered by the **Department of Computer Science, University of Science (VNU-HCMC)**.

- The first milestone focuses on **data scraping engineering** – transforming theoretical knowledge of data crawling into practical implementation by harvesting academic papers from **arXiv**, an open-access scientific repository.

---

## 👨‍💻 Executor

| Name                   | Student ID | 
| ---------------------- | ---------- | 
| **Lê Minh Nhật** | 23120067   |

---

## 🎯 Milestone 1: Data Scraping and Repository Engineering

Milestone 1 enables students to:

* Implement **concurrent web scraping** techniques to retrieve structured data at scale and understand the **engineering workflow** of building a data collection pipeline from open APIs.
* Practice handling **large datasets** and **metadata organization**.
* Integrate and synchronize multiple data sources: **arXiv API**, **OAI-PMH**, **Semantic Scholar API**, and **Kaggle dataset**.

---

## ⚙️ Tools and Technologies

| Tool / Library                   | Purpose                                                            |
| -------------------------------- | ------------------------------------------------------------------ |
| **arXiv API (arxiv.py)**         | Retrieve paper metadata, version history, and source downloads     |
| **Semantic Scholar Graph API**   | Extract paper references, citation graphs, and related identifiers |
| **Python requests + Session**    | HTTP requests with connection pooling and retry mechanisms         |
| **BeautifulSoup4**               | HTML parsing for version date extraction                           |
| **concurrent.futures**           | Multi-threaded parallel processing for papers and downloads        |
| **psutil + tracemalloc**         | Memory and performance monitoring                                  |
| **tqdm**                         | Progress bar visualization                                         |

---

## 🧵 Concurrent Data Pipeline

To ensure efficiency and scalability, the pipeline employs **concurrent processing** at multiple levels:

1. **Paper-Level Parallelism**
   * Processes multiple papers simultaneously using ThreadPoolExecutor
   * Configurable workers (default: 12 papers in parallel)
   * Each paper handled independently to prevent blocking

2. **Version Discovery**
   * Concurrent HEAD requests to check version existence
   * Up to 6 threads checking versions simultaneously
   * Smart contiguous version detection (v1, v2, v3...)

3. **Download Optimization**
   * Parallel downloads of multiple versions per paper
   * Configurable download workers (default: 4 per paper)
   * Session pooling with connection reuse

4. **Reference Extraction**
   * Single Semantic Scholar API call fetches both references AND venue
   * LRU cache prevents duplicate API calls
   * Built-in rate limiter (2 req/sec, 200 req/5min)

This modularized and concurrent design supports fault isolation, improved runtime performance, and higher throughput for large datasets.

---

## 📂 Project Structure

The folder hierarchy follows the required format from the course guideline:

```
23120067/
│
├── README.md                       # This documentation file
│
├── src/
│   ├── scraping_arxiv.py               # Main scraper implementation
│   └── requirements.txt            # Python dependencies
│
├── Report.pdf                      # Technical report with methodology & performance
│
├── 23120067/                       # Output data directory (student ID folder) 
│   ├── 2504-13946/                 # Paper folder (yymm-id format)
│   │   ├── metadata.json           # Paper metadata
│   │   ├── references.json         # Extracted references with arXiv IDs
│   │   └── tex/                    # TeX sources directory
│   │       ├── 2504-13946v1/       # Version 1
│   │       │   ├── main.tex
│   │       │   ├── references.bib
│   │       │   └── sections/
│   │       │       └── intro.tex
│   │       └── 2504-13946v2/       # Version 2
│   │           └── ...
│   │
│   ├── 2504-13947/                 # Another paper
│   │   ├── metadata.json
│   │   ├── references.json
│   │   └── tex/
│   │       └── 2504-13947v1/
│   │
│   └── performance_report_23120067.json  # Auto-generated performance metrics
│
└── scraper_optimized_report.log    # Detailed execution logs
```

---

## 🧰 Environment Setup

**System Requirements:**
- **Python**: 3.8 or higher
- **RAM**: Minimum 4GB (recommended 8GB for optimal performance)
- **Disk**: At least 1GB free space (depends on number of papers)
- **Internet**: Stable connection for API requests

**Installation Steps:**

1. **Navigate to project directory**
```bash
cd 23120067
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

Or install manually:
```bash
pip install arxiv>=2.0.0 requests>=2.28.0 beautifulsoup4>=4.11.0 tqdm>=4.64.0 psutil>=5.9.0 pandas>=2.0.0
```


3. **Verify installation**
```python
python -c "import arxiv, requests, bs4, tqdm, psutil, pandas; print('✓ All dependencies installed successfully!')"
```

---

## 🚀 Usage Guide

### Mode 1: Default Configuration (Recommended for Google Colab)
```bash
python src/scraping_arxiv.py
```

**Default Configuration:**
- Student ID: `23120067`
- ArXiv ID range: `2504.13946` to `2504.15000` (you can choose your another range)
- Output directory: `./23120067`
- Paper-level workers: 12 (concurrent papers)
- Download workers: 4 (concurrent version downloads)

### Mode 2: Custom Parameters

#### Option A: Scrape by ID Range
```bash
python src/scraping_arxiv.py \
  --student 23120067 \
  --range 2504.13946 2504.14000 \
  --out ./output_folder \
  --workers 8 \
  --dworkers 3
```

#### Option B: Scrape Specific Paper IDs
```bash
python src/scraping_arxiv.py \
  --student 23120067 \
  --ids 2504.13946 2504.13950 2504.13955 \
  --out ./my_papers
```

### Command-Line Parameters

| Parameter | Required | Description | Example |
|---------|----------|-------|-------|
| `--student` | ✅ | Student ID | `--student 23120067` |
| `--range` | ✅* | ArXiv ID range (start end) | `--range 2504.13946 2504.15000` |
| `--ids` | ✅* | Specific list of IDs | `--ids 2504.13946 2504.13950` |
| `--out` | ❌ | Output directory (default: student_id) | `--out ./data` |
| `--workers` | ❌ | Parallel paper processing (default: 12) | `--workers 8` |
| `--dworkers` | ❌ | Parallel version downloads (default: 4) | `--dworkers 3` |

*Note: Must use either `--range` OR `--ids`, not both*

---

## 🔧 Advanced Configuration

Edit constants in `scraping_arxiv.py` for fine-tuning:

```python
# Scraping speed control
DEFAULT_MAX_PAPER_WORKERS = 12      # Papers processed in parallel
DEFAULT_DOWNLOAD_WORKERS = 4         # Version downloads per paper
VERSION_HEAD_THREADS = 6             # Concurrent version checks

# Semantic Scholar API rate limits
SS_REQUESTS_PER_SECOND = 2           # Requests per second
SS_REQUESTS_PER_5_MIN = 200          # Requests per 5-minute window

# Timeout settings
DEFAULT_SESSION_TIMEOUT = 15         # Seconds
MAX_VERSION_TO_CHECK = 8             # Maximum versions to discover
```

---

## 📊 Output Data Format

### JSON File Formats

#### `metadata.json` - Paper Metadata
```json
{
  "paper_title": "Example Paper Title",
  "authors": ["Author One", "Author Two"],
  "submission_date": "2025-04-15",
  "revised_dates": ["2025-04-15", "2025-04-20"],
  "publication_venue": "ICML 2025"
}
```

#### `references.json` - Citation References
```json
{
  "2504-12345": {
    "paper_title": "Referenced Paper",
    "authors": ["Ref Author"],
    "submission_date": "2025-03-01",
    "semantic_scholar_id": "abc123def456"
  }
}
```

---

## 📈 Monitoring & Logs

### Log Files
- **`scraper_optimized_report.log`**: Detailed execution trace with timestamps
- **Console output**: Real-time progress bars and status updates

### Performance Report
The auto-generated `performance_report_{student_id}.json` contains:

| Metric Category | Includes |
|----------------|----------|
| **Scraping Statistics** | Success rate, papers processed, failures |
| **Time Analysis** | Total runtime, avg per paper, breakdown by phase |
| **Size Metrics** | Average size before/after cleanup, final output size |
| **Memory Usage** | Peak memory, average consumption |
| **API Statistics** | Semantic Scholar requests, rate limit hits |
| **Reference Data** | Total references, arXiv ID extraction rate |

---

## ⚠️ Common Issues & Solutions

### Issue 1: Missing Dependencies
**Error:** `ModuleNotFoundError`

**Solution:**
### Issue 2: Semantic Scholar Rate Limiting
**Warning:** `Rate limit window full, sleeping XX.Xs`

**Solution:** This is expected behavior. The scraper automatically handles rate limits by pausing requests. No action needed.

### Issue 3: Connection Timeouts
**Error:** `Download exception for XXXX: timeout`

**Solutions:**
- Increase `DEFAULT_SESSION_TIMEOUT` in source code
- Reduce `--workers` parameter
- Check internet connection stability

### Issue 4: Out of Memory
**Error:** `MemoryError`

**Solutions:**
- Reduce `--workers` (try 4-6)
- Reduce `--dworkers` (try 2-3)
- Close other applications
- Use environment with more RAM

### Issue 5: Empty Version Folders
**Observation:** Some version folders contain no .tex files

**Explanation:** This is expected. Not all arXiv papers provide LaTeX sources (some only have PDFs). Empty folders are retained per course requirements.

---

## 🎯 Performance Optimization Tips

### Configuration for Personal Computer (8GB+ RAM)
```bash
python src/scraping_arxiv.py --student 23120067 --range START END --workers 16 --dworkers 6
```

### Configuration for Google Colab (Free Tier)
```bash
python src/scraping_arxiv.py --student 23120067 --range START END --workers 8 --dworkers 3
```

### Configuration for Slow Network Connection
```bash
python src/scraping_arxiv.py --student 23120067 --range START END --workers 4 --dworkers 2
```

---

## 📊 Evaluation Metrics

Each scraper execution is evaluated using the following criteria (as required by the course specification):

| Category | Metric |
| --------------------- | -------------------------------------------- |
| **Data Completeness** | Number of papers scraped successfully |
| **Success Rate** | Percentage of papers processed without errors |
| **Metadata Coverage** | Ratio of successfully retrieved metadata entries |
| **Performance** | Average runtime per paper and memory footprint |
| **Data Efficiency** | Storage reduction after removing figures |
| **Reference Quality** | Success rate of arXiv ID extraction from references |
| **Code Quality** | Readability, documentation, and proper error handling |

---

## 📦 Deliverables

✅ **Source Code**
- All `.py` files organized under `src/`
- Clean, documented, and runnable code
- `requirements.txt` for environment reproduction

✅ **Dataset**
- Compressed `.zip` file named `<StudentID>.zip`
- Follows required folder structure (yymm-id format)
- Contains metadata.json, references.json, and tex/ for each paper

✅ **Technical Report**
- Implementation methodology and design decisions
- Performance analysis with metrics from auto-generated report
- Statistics: success rate, processing time, memory usage, reference extraction

✅ **Demo Video**
- Maximum 120 seconds (2 minutes)
- Demonstrates code execution and output
- Voice explanation of scraper design and workflow
- Must remain publicly viewable for 1 month after course completion

---

## 📈 Benchmark Results

Based on actual execution (`performance_report_23120067.json`):

| Metric | Value |
|--------|-------|
| **Papers Successfully Scraped** | 1,055 / 1,055 (100.0%) |
| **Total Runtime** | 2,488.3 seconds (~41.5 minutes) |
| **Average Time per Paper** | 2.36 seconds (parallel execution) |
| **Average Sequential Time** | 28.20 seconds (if processed serially) |
| **Peak Memory Usage** | 399.89 MB |
| **Average Memory Usage** | 236.53 MB |
| **Final Output Size** | 307.6 MB (322,560,867 bytes) |
| **Max Disk Usage** | 411.9 MB (432,046,747 bytes) |
| **Versions Found** | 1,618 versions across all papers |
| **Versions Downloaded** | 1,343 (83.0% success rate) |
| **References Found** | 895 references |
| **References with arXiv IDs** | 310 (34.64% extraction rate) |
| **Semantic Scholar API Calls** | 2,159 requests |
| **Rate Limit Hits** | 0 (proper rate limiting) |

**Performance Highlights:**
- ⚡ **11.9x speedup** compared to sequential processing (28.2s vs 2.36s per paper)
- 🎯 **100% success rate** for paper scraping
- 💾 **Memory efficient**: Peak usage under 400 MB for 1,055 papers
- 🔄 **Zero rate limit violations** with built-in rate limiter

---

## 🔍 Troubleshooting Guide

### Debugging Steps

**1. Script Won't Run**
- Check Python version: `python --version` (requires ≥ 3.8)
- Verify dependencies: `pip list | grep -E "arxiv|requests|beautifulsoup4"`
- Test imports: `python -c "import arxiv; print('OK')"`

**2. Missing or Incomplete Data**
- Review log file: `scraper_optimized_report.log`
- Check console warnings for specific errors
- Verify arXiv IDs are valid (format: YYMM.NNNNN)

**3. Poor Performance**
- Reduce workers if CPU/RAM limited
- Test network speed: `ping arxiv.org`
- Check rate limit statistics in performance report

---

## ⚡ Implementation Highlights

**Key Optimizations:**
1. **Session Pooling**: Reuses HTTP connections for faster requests
2. **LRU Cache**: Prevents duplicate API calls to Semantic Scholar
3. **Smart Rate Limiting**: Respects API limits without manual intervention
4. **Parallel Processing**: Multi-level concurrency (papers + versions + checks)
5. **Memory Management**: Automatic cleanup of figures and temporary files
6. **Fault Tolerance**: Graceful error handling with detailed logging

**Design Decisions:**
- Single Semantic Scholar call per paper fetches both references AND venue
- HEAD requests for version discovery (faster than full downloads)
- Concurrent version downloads per paper for maximum throughput
- Automatic retry mechanism with exponential backoff

---

## 📋 Important Notes

1. **Source File Integrity** - .tex and .bib files are preserved exactly as in original sources
2. **Automatic Figure Removal** - Non-TeX/BIB files are automatically deleted to reduce size
3. **Empty Version Folders** - Normal for papers without LaTeX sources; folders retained per requirements
4. **API Rate Limiting** - Automatic compliance with Semantic Scholar limits (2 req/sec, 200 req/5min)
5. **Memory Tracking** - Built-in monitoring with psutil and tracemalloc
6. **Performance Reporting** - Automatic generation of comprehensive metrics JSON

---

## 🤝 Support & Contact

**For Technical Issues:**
1. Check the detailed log file: `scraper_optimized_report.log`
2. Review the demo video (link provided in Report.docx)
3. Contact instructor: **Huỳnh Lâm Hải Đăng** at hlhdang@fit.hcmus.edu.vn

**For Course Questions:**
- Use the provided ZALO group
- Office hours as announced

---

## 📚 References

1. Waleed Ammar et al., *The Semantic Scholar Open Research Corpus*, [arXiv:1805.02234](https://arxiv.org/abs/1805.02234)
2. [arXiv API Basics](https://info.arxiv.org/help/api/basics.html)
3. [arXiv API User's Manual](https://info.arxiv.org/help/api/user-manual.html)
4. [Semantic Scholar Graph API Documentation](https://api.semanticscholar.org/api-docs/graph)
5. [Cornell University arXiv Dataset on Kaggle](https://www.kaggle.com/datasets/Cornell-University/arxiv)
6. [arxiv.py - Python Wrapper for arXiv API](https://github.com/lukasschwab/arxiv.py)
7. [BeautifulSoup4 Documentation](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)

---

## 📄 License & Academic Integrity

This project is submitted for academic evaluation as part of the **Introduction to Data Science** course.

- **Course**: Introduction to Data Science (Milestone 1)
- **Institution**: Faculty of Information Technology, University of Science (VNU-HCMC)
- **Instructor**: Huỳnh Lâm Hải Đăng
- **Academic Year**: 2025-2026

**Academic Integrity Statement:**
- All code is original work or properly cited
- External references and libraries are documented
- Collaboration limited to course-approved discussion
- No plagiarism or unauthorized code sharing

---

**© 2025 University of Science (VNU-HCMC)**  
*Developed for Introduction to Data Science – Milestone 1*