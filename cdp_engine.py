"""
ApplyBot CDP Engine — Real Chrome DevTools Protocol automation for LinkedIn Easy Apply.
Connects to Chrome via remote debugging (port 9224).
"""
import json, time, re, logging, urllib.request, socket, ssl, uuid

CDP_HOST = "localhost"
CDP_PORT = 9224

log = logging.getLogger("cdp_engine")


def _get_tab(url_pattern: str = None) -> dict:
    """Find a tab matching the URL pattern. Returns the first matching tab."""
    resp = urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5)
    tabs = json.loads(resp.read())
    for tab in tabs:
        if tab.get("type") != "page":
            continue
        if url_pattern and url_pattern not in tab.get("url", ""):
            continue
        return tab
    # Return first page tab if no match
    for tab in tabs:
        if tab.get("type") == "page":
            return tab
    return None


class CDPClient:
    """Minimal CDP client over raw TCP + WebSocket upgrade."""
    
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.sock = None
        self._msg_id = 0
        
    def connect(self):
        """Establish WebSocket connection via HTTP upgrade."""
        import re
        host = CDP_HOST
        port = CDP_PORT
        path = self.ws_url.split(f"ws://{host}:{port}")[1] if f"ws://{host}:{port}" in self.ws_url else "/"
        
        key = "dGhlIHNhbXBsZSBub25jZQ=="  # base64("the sample nonce")
        
        # Raw TCP + WebSocket upgrade handshake
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(15)
        self.sock.connect((host, port))
        
        if port == 9224 and not host.startswith("wss"):
            # Plain TCP upgrade
            upgrade = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            )
            self.sock.send(upgrade.encode())
            response = self.sock.recv(4096)
            if b"101" not in response:
                raise Exception(f"WebSocket upgrade failed: {response[:200]}")
            log.debug("WebSocket connected")
    
    def _send_frame(self, payload: bytes, opcode: int = 0x1):
        """Send a WebSocket text frame (no masking needed for server->client, but client->server requires mask)."""
        import struct, os
        # Client MUST mask frames
        mask_key = os.urandom(4)
        masked = bytearray(payload)
        for i in range(len(masked)):
            masked[i] ^= mask_key[i % 4]
        
        frame = bytearray()
        frame.append(0x80 | opcode)  # FIN + opcode
        length = len(payload)
        if length < 126:
            frame.append(0x80 | length)  # MASK bit set
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", length))
        frame.extend(mask_key)
        frame.extend(masked)
        self.sock.send(bytes(frame))
    
    def _recv_frame(self) -> str:
        """Receive and decode a WebSocket text frame."""
        import struct
        # Read first 2 bytes
        header = self.sock.recv(2)
        if len(header) < 2:
            raise ConnectionError("Socket closed")
        
        opcode = header[0] & 0x0F
        masked = (header[1] & 0x80) != 0
        length = header[1] & 0x7F
        
        if length == 126:
            length = struct.unpack(">H", self.sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self.sock.recv(8))[0]
        
        if masked:
            mask = self.sock.recv(4)
        
        data = bytearray()
        while len(data) < length:
            chunk = self.sock.recv(min(length - len(data), 65536))
            if not chunk:
                break
            data.extend(chunk)
        
        if masked:
            for i in range(len(data)):
                data[i] ^= mask[i % 4]
        
        if opcode == 0x8:  # Close
            raise ConnectionError("WebSocket closed by server")
        
        return data.decode("utf-8", errors="replace")
    
    def send(self, method: str, params: dict = None) -> dict:
        """Send a CDP command and return the result."""
        self._msg_id += 1
        msg = json.dumps({
            "id": self._msg_id,
            "method": method,
            "params": params or {}
        })
        self._send_frame(msg.encode())
        
        # Read response (may include events before the result)
        while True:
            resp = json.loads(self._recv_frame())
            if resp.get("id") == self._msg_id:
                if "error" in resp:
                    raise Exception(f"CDP error: {resp['error']}")
                return resp.get("result", {})
            # else: it's an event, ignore for sync commands
    
    def evaluate(self, expression: str) -> dict:
        """Execute JavaScript in the page."""
        return self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True
        })
    
    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except:
                pass


def navigate(url: str) -> CDPClient:
    """Navigate to a URL and return a connected CDP client."""
    tab = _get_tab()
    if not tab:
        raise Exception("No Chrome tab found")
    
    # Use HTTP endpoint to navigate
    req = urllib.request.Request(
        f"http://{CDP_HOST}:{CDP_PORT}/json/navigate?url={urllib.request.quote(url)}",
        data=json.dumps({"url": url}).encode(),
        method="PUT"
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except:
        pass
    
    time.sleep(3)  # Wait for navigation
    
    # Connect via WebSocket
    tab = _get_tab()
    ws_url = tab["webSocketDebuggerUrl"]
    client = CDPClient(ws_url)
    client.connect()
    
    # Enable necessary domains
    client.send("Page.enable")
    client.send("Runtime.enable")
    
    return client


# ============================================================
#  LINKEDIN EASY APPLY AUTOMATION
# ============================================================

def find_easy_apply_jobs(client: CDPClient, keywords: str, location: str, max_jobs: int = 10) -> list:
    """Search LinkedIn Jobs and return Easy Apply listings.
    S7 Netzach: multiple selector fallbacks for LinkedIn DOM changes."""
    encoded_kw = urllib.request.quote(keywords)
    encoded_loc = urllib.request.quote(location)
    search_url = (
        f"https://www.linkedin.com/jobs/search/"
        f"?keywords={encoded_kw}&location={encoded_loc}"
        f"&f_AL=true"  # Easy Apply filter
        f"&f_E=1,2"    # Entry + Associate level
    )
    
    # Navigate
    client.send("Page.navigate", {"url": search_url})
    time.sleep(4)
    
    # Extract job cards with MULTIPLE FALLBACK SELECTORS (S7)
    extract_js = """
    (function() {
        // LinkedIn changes their DOM class names frequently.
        // Try multiple selector strategies in priority order.
        const strategies = [
            // Strategy 1: Current standard job card selectors
            () => document.querySelectorAll('.job-card-container'),
            // Strategy 2: Search results list items
            () => document.querySelectorAll('.jobs-search-results__list-item'),
            // Strategy 3: Generic list items in job search results
            () => document.querySelectorAll('[data-job-id]'),
            // Strategy 4: Any list item containing job title links
            () => document.querySelectorAll('li:has(a[href*="/jobs/view/"])'),
        ];
        
        let cards = null;
        let usedStrategy = 0;
        for (let i = 0; i < strategies.length; i++) {
            const result = strategies[i]();
            if (result && result.length > 0) {
                cards = result;
                usedStrategy = i;
                break;
            }
        }
        
        if (!cards || cards.length === 0) {
            return {jobs: [], total: 0, error: 'No job cards found with any selector', usedStrategy: -1};
        }
        
        const jobs = [];
        cards.forEach((card, i) => {
            // Try multiple title selectors
            let title = '';
            const titleSelectors = [
                '.job-card-list__title',
                '.job-card-container__primary-description',
                '.job-card-list__title-link',
                'a[href*="/jobs/view/"]',
                '.artdeco-entity-lockup__title',
            ];
            for (const sel of titleSelectors) {
                const el = card.querySelector(sel);
                if (el && el.innerText.trim()) {
                    title = el.innerText.trim();
                    break;
                }
            }
            
            // Try multiple company selectors
            let company = '';
            const companySelectors = [
                '.job-card-container__company-name',
                '.job-card-subtitle',
                '.artdeco-entity-lockup__subtitle',
                '[class*="company"]',
            ];
            for (const sel of companySelectors) {
                const el = card.querySelector(sel);
                if (el && el.innerText.trim()) {
                    company = el.innerText.trim();
                    break;
                }
            }
            
            let link = '';
            const linkEl = card.querySelector('a[href*="/jobs/view/"]') || 
                          card.querySelector('a.job-card-list__title') ||
                          card.querySelector('a.job-card-container__link');
            if (linkEl) link = linkEl.href;
            
            // Check Easy Apply
            const easyApply = !!card.querySelector('[class*="easy-apply"]') ||
                             (card.innerText || '').includes('Easy Apply') ||
                             !!card.querySelector('[aria-label*="Easy Apply"]');
            
            if (title && easyApply) {
                jobs.push({title, company, link, index: i});
            }
        });
        return {jobs, total: cards.length, usedStrategy};
    })()
    """
    
    result = client.evaluate(extract_js)
    
    value = result.get("result", {}).get("value", {})
    jobs = value.get("jobs", [])
    strategy = value.get("usedStrategy", -1)
    error = value.get("error")
    
    if error:
        log.warning(f"Job extraction warning: {error}")
    log.info(f"Found {len(jobs)} Easy Apply jobs (strategy #{strategy}, "
             f"scanned {value.get('total', 0)} cards)")
    
    return jobs[:max_jobs]


def apply_to_job(client: CDPClient, job: dict, user_data: dict) -> dict:
    """
    Apply to a single LinkedIn Easy Apply job.
    S7 Netzach: multiple selector fallbacks for button/form DOM changes.
    S5 Gevurah: random delays to avoid bot detection.
    
    user_data should contain:
        - name, email, phone
        - resume_path (local file path)
        - cover_letter (optional)
    """
    job_title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    job_url = job.get("link", "")
    
    log.info(f"Applying to: {job_title} @ {company}")
    
    if not job_url:
        return {"success": False, "error": "No job URL"}
    
    # Navigate to job page
    client.send("Page.navigate", {"url": job_url})
    time.sleep(3)
    
    # Anti-detection: random scroll to simulate human
    client.evaluate("window.scrollTo(0, {amount})".format(
        amount=hash(job_title) % 300 + 100
    ))
    time.sleep(0.5)
    
    # Click Easy Apply button with MULTIPLE FALLBACKS (S7)
    clicked = client.evaluate("""
    (function() {
        // Try every possible Easy Apply button selector
        const selectors = [
            '.jobs-apply-button--top-card button',
            '[aria-label*="Easy Apply"]',
            '.jobs-apply-button',
            '.jobs-s-apply button',
            'button.jobs-apply-button',
            'button[class*="jobs-apply"]',
            // Generic: any button containing "Easy Apply" or "Apply"
            function() {
                const allBtns = document.querySelectorAll('button');
                for (const btn of allBtns) {
                    if (/easy.*apply|apply.*now/i.test(btn.innerText)) return btn;
                }
                return null;
            }
        ];
        for (const sel of selectors) {
            try {
                const btn = typeof sel === 'function' ? sel() : document.querySelector(sel);
                if (btn) {
                    btn.click();
                    return true;
                }
            } catch(e) {}
        }
        return false;
    })()
    """)
    
    if not clicked.get("result", {}).get("value", False):
        return {"success": False, "error": "Easy Apply button not found"}
    
    time.sleep(2)
    
    # Process multi-step Easy Apply form
    for step in range(5):  # Max 5 steps
        # Anti-detection: variable delay between steps
        import random
        time.sleep(1.5 + random.uniform(0.3, 1.5))
        
        # Check current step
        state = client.evaluate("""
        (function() {
            const modal = document.querySelector('.jobs-easy-apply-modal, .artdeco-modal');
            if (!modal) return {done: true, reason: 'no modal'};
            
            const progress = modal.querySelector('.artdeco-completeness-meter-linear__progress-value')?.innerText || '';
            const header = modal.querySelector('.jobs-easy-apply-modal__title, h2, h3')?.innerText?.trim() || '';
            
            // Find all buttons in modal
            const buttons = modal.querySelectorAll('button');
            let submitBtn = null, nextBtn = null, reviewBtn = null;
            for (const btn of buttons) {
                const text = (btn.innerText || '').trim().toLowerCase();
                const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                if (text.includes('submit') || label.includes('submit')) submitBtn = true;
                if (text.includes('review') || label.includes('review')) reviewBtn = true;
                if (text.includes('next') || text.includes('continue') || 
                    label.includes('next') || label.includes('continue')) nextBtn = true;
            }
            
            return {
                progress, header,
                hasSubmit: !!submitBtn,
                hasNext: !!nextBtn,
                hasReview: !!reviewBtn,
                modalVisible: !!modal
            };
        })()
        """)
        
        state_val = state.get("result", {}).get("value", {})
        log.info(f"  Step {step+1}: {state_val.get('header', '?')} | "
                 f"progress={state_val.get('progress', '?')}")
        
        if state_val.get("done") or not state_val.get("modalVisible"):
            break
        
        # Try to fill form fields
        fill_js = """
        (function() {
            const fields = document.querySelectorAll(
                '.jobs-easy-apply-modal input:not([type="hidden"]), ' +
                '.jobs-easy-apply-modal textarea, ' +
                '.jobs-easy-apply-modal select'
            );
            const filled = [];
            const userName = \"""" + (user_data.get("name", "").replace('"', '\\"')) + """\";
            const userEmail = \"""" + (user_data.get("email", "").replace('"', '\\"')) + """\";
            const userPhone = \"""" + (user_data.get("phone", "").replace('"', '\\"')) + """\";
            
            fields.forEach(f => {
                const label = (f.getAttribute('aria-label') || f.name || f.placeholder || '').toLowerCase();
                
                // Simulate human typing with value setting + input event
                function setValue(el, val) {
                    el.focus();
                    el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                }
                
                if (label.includes('name') || label.includes('full name') || label.includes('first')) {
                    setValue(f, userName);
                    filled.push('name');
                } else if (label.includes('email')) {
                    setValue(f, userEmail);
                    filled.push('email');
                } else if (label.includes('phone')) {
                    setValue(f, userPhone);
                    filled.push('phone');
                } else if (label.includes('city') || label.includes('location')) {
                    setValue(f, 'Remote');
                    filled.push('location');
                } else if (f.type === 'number' && label.includes('year')) {
                    setValue(f, '5');
                    filled.push('years-experience');
                }
            });
            
            // Auto-select first option for radio groups (typically "Yes" answers)
            const radioGroups = document.querySelectorAll('.jobs-easy-apply-modal fieldset');
            radioGroups.forEach(group => {
                const firstRadio = group.querySelector('input[type="radio"]');
                if (firstRadio && !firstRadio.checked) {
                    firstRadio.click();
                    filled.push('radio-selected');
                }
            });
            
            // Check for resume upload
            const fileInput = document.querySelector('.jobs-easy-apply-modal input[type="file"]');
            if (fileInput && (!fileInput.files || fileInput.files.length === 0)) {
                filled.push('resume-upload-available');
            }
            
            return {filled, fieldCount: fields.length};
        })()
        """
        
        fill_result = client.evaluate(fill_js)
        log.info(f"  Filled: {fill_result.get('result', {}).get('value', {}).get('filled', [])}")
        
        # Click Next/Review/Submit with fallbacks
        clicked_btn = client.evaluate("""
        (function() {
            const btns = document.querySelectorAll('.jobs-easy-apply-modal button, .artdeco-modal button');
            const allTexts = [];
            
            // Priority: Submit > Review > Next
            for (const btn of btns) {
                const text = (btn.innerText || '').trim();
                const label = btn.getAttribute('aria-label') || '';
                allTexts.push(text + '|' + label);
                
                if (text === 'Submit application' || label.includes('Submit') || 
                    text === 'Submit') {
                    btn.click(); return 'submit';
                }
            }
            for (const btn of btns) {
                const text = (btn.innerText || '').trim();
                const label = btn.getAttribute('aria-label') || '';
                if (text === 'Review' || label.includes('Review') ||
                    text === 'Review application') {
                    btn.click(); return 'review';
                }
            }
            for (const btn of btns) {
                const text = (btn.innerText || '').trim();
                const label = btn.getAttribute('aria-label') || '';
                if (text === 'Next' || label.includes('Next') || 
                    label.includes('Continue') || text === 'Continue') {
                    btn.click(); return 'next';
                }
            }
            
            // Fallback: click the last non-dismiss button
            for (let i = btns.length - 1; i >= 0; i--) {
                const text = (btns[i].innerText || '').trim();
                if (text && !text.includes('Dismiss') && !text.includes('Close') && !text.includes('X')) {
                    btns[i].click();
                    return 'fallback-last-btn:' + text;
                }
            }
            
            return 'none|buttons:' + allTexts.join(',');
        })()
        """)
        
        action = clicked_btn.get("result", {}).get("value", "none")
        log.info(f"  Clicked: {action}")
        
        if action.startswith("submit") or action.startswith("none"):
            break
    
    # Check result
    time.sleep(2)
    result = client.evaluate("""
    (function() {
        const bodyText = document.body.innerText || '';
        const success = document.querySelector('.jobs-easy-apply-modal__success') ||
                       document.querySelector('[data-test-modal-container] .artdeco-inline-feedback--success') ||
                       document.querySelector('.artdeco-inline-feedback--success');
        const error = document.querySelector('.artdeco-inline-feedback--error');
        const confirmation = bodyText.includes('Application sent') || 
                            bodyText.includes('Your application was sent') ||
                            bodyText.includes('application has been submitted') ||
                            bodyText.includes('Applied');
        return {
            success: !!success || confirmation,
            error: error?.innerText?.trim() || null,
            confirmation,
            bodySnippet: bodyText.substring(0, 200)
        };
    })()
    """)
    
    result_val = result.get("result", {}).get("value", {})
    
    if result_val.get("success") or result_val.get("confirmation"):
        log.info(f"  ✓ Applied successfully: {job_title} @ {company}")
        return {
            "success": True,
            "job_title": job_title,
            "company": company,
            "url": job_url
        }
    else:
        log.warning(f"  ✗ Application may not have submitted: "
                    f"{result_val.get('error', 'unknown')} | "
                    f"body: {result_val.get('bodySnippet', '?')[:80]}")
        return {
            "success": False,
            "job_title": job_title,
            "company": company,
            "url": job_url,
            "error": result_val.get("error", "Confirmation not detected"),
            "debug_snippet": result_val.get("bodySnippet", "")
        }


def apply_to_jobs(user_data: dict, keywords: str, location: str, max_applications: int = 5) -> list:
    """
    Full flow: search jobs → find Easy Apply → apply to each.
    """
    client = navigate("https://www.linkedin.com/jobs/")
    
    jobs = find_easy_apply_jobs(client, keywords, location, max_applications)
    log.info(f"Found {len(jobs)} Easy Apply jobs for '{keywords}' in {location}")
    
    results = []
    for job in jobs[:max_applications]:
        try:
            result = apply_to_job(client, job, user_data)
            results.append(result)
            if result.get("success"):
                time.sleep(2)  # Rate limit between applications
        except Exception as e:
            log.error(f"Failed to apply to {job.get('title')}: {e}")
            results.append({"success": False, "job_title": job.get("title"), "error": str(e)})
    
    client.close()
    return results


# ============================================================
#  TEST
# ============================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [cdp] %(message)s")
    
    # Test data
    test_user = {
        "name": "Richard DeJesus",
        "email": "Dejesusrichard89@gmail.com",
        "phone": "(404) 840-9115",
        "resume_path": "C:/Users/dejes/applybot/data/Richard DeJesus Resume 6_25.pdf"
    }
    
    # Quick test: just open LinkedIn and verify we're logged in
    client = navigate("https://www.linkedin.com/feed/")
    logged_in = client.evaluate("document.querySelector('.global-nav__me') ? 'YES' : 'NO'")
    print(f"Logged in: {logged_in.get('result', {}).get('value', 'UNKNOWN')}")
    client.close()
