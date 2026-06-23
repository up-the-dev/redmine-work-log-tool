import http.server
import socketserver
import json
import os
import subprocess
import datetime
import urllib.request
import urllib.error

def load_env():
    env_vars = {}
    if os.path.exists(".env"):
        try:
            with open(".env", "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        val = val.strip().strip("'\"")
                        env_vars[key.strip()] = val
        except Exception as e:
            print(f"Error reading .env: {e}")
    for k, v in os.environ.items():
        env_vars[k] = v
    return env_vars

ENV = load_env()
PORT = int(ENV.get("PORT", 8080))

def log_info(msg):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] {msg}", flush=True)

def log_error(msg):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] {msg}", flush=True)


class WorklogHandler(http.server.BaseHTTPRequestHandler):
    def end_headers(self):
        # Allow CORS for development if needed
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                with open("index.html", "rb") as f:
                    self.wfile.write(f.read())
            except FileNotFoundError:
                self.wfile.write(b"<h1>index.html not found. Make sure it is in the same directory.</h1>")
        elif self.path == "/api/config":
            env = load_env()
            config_data = {
                "redmine_url": env.get("REDMINE_URL", "https://pm.shauryatechnosoft.com"),
                "redmine_api_key": env.get("REDMINE_API_KEY", ""),
                "project_id": env.get("PROJECT_ID", "8"),
                "git_email": env.get("GIT_AUTHOR_EMAIL", ""),
                "gemini_api_key": env.get("GEMINI_API_KEY", ""),
                "workspaces": env.get("WORKSPACE_PATHS", '["/home/umesh-pawar/workspace/fleet-express/ui", "/home/umesh-pawar/workspace/fleet-express/fleet-cloud-side"]')
            }
            self.send_json_response(config_data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        
        try:
            req_data = json.loads(post_data.decode('utf-8'))
        except Exception as e:
            self.send_error_response(400, f"Invalid JSON payload: {str(e)}")
            return

        if self.path == "/api/fetch-commits":
            self.handle_fetch_commits(req_data)
        elif self.path == "/api/plan-worklog":
            self.handle_plan_worklog(req_data)
        elif self.path == "/api/push-redmine":
            self.handle_push_redmine(req_data)
        elif self.path == "/api/update-status":
            self.handle_update_status(req_data)
        else:
            self.send_response(404)
            self.end_headers()

    def send_json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def send_error_response(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode('utf-8'))

    def handle_fetch_commits(self, data):
        workspace_paths = data.get("workspace_paths", [])
        author_email = data.get("author_email", "").strip()
        since_date = data.get("since_date", "")
        until_date = data.get("until_date", "")

        log_info(f"Incoming fetch-commits request. Git Email: '{author_email}', Scrap Range: {since_date} to {until_date}")

        if not workspace_paths:
            log_error("Fetch-commits request rejected: Workspace paths are missing.")
            self.send_error_response(400, "Workspace paths are required.")
            return

        all_commits = []
        for path in workspace_paths:
            path = os.path.expanduser(path.strip())
            log_info(f"Scanning workspace path: {path}")
            if not os.path.exists(path):
                log_error(f"Workspace path does not exist: {path}")
                continue
            
            # Identify sub-directories that are git repos or the directory itself
            git_dirs = []
            if os.path.exists(os.path.join(path, ".git")):
                git_dirs.append(path)
            else:
                for sub in os.listdir(path):
                    sub_path = os.path.join(path, sub)
                    if os.path.isdir(sub_path) and os.path.exists(os.path.join(sub_path, ".git")):
                        git_dirs.append(sub_path)

            log_info(f"Found {len(git_dirs)} active Git repositories under {path}")

            for repo in git_dirs:
                repo_name = os.path.basename(repo)
                try:
                    cmd = ["git", "log", f"--since={since_date}"]
                    if until_date:
                        cmd.append(f"--until={until_date}")
                    if author_email:
                        cmd.append(f"--author={author_email}")
                    
                    cmd.append("--pretty=format:%H|%ai|%an|%s")
                    cmd.append("--date=iso")

                    log_info(f"Executing git log command in repository: {repo_name}")
                    res = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=True)
                    output = res.stdout.strip()
                    if output:
                        lines = output.split("\n")
                        log_info(f"Scraped {len(lines)} raw commits in {repo_name}")
                        for line in lines:
                            parts = line.split("|", 3)
                            if len(parts) == 4:
                                commit_hash, date_str, author, message = parts
                                dt_str = date_str.split()[0]
                                all_commits.append({
                                    "repo": repo_name,
                                    "hash": commit_hash[:8],
                                    "date": dt_str,
                                    "author": author,
                                    "message": message
                                })
                except Exception as e:
                    log_error(f"Error scanning git in repository '{repo_name}': {e}")

        # Sort commits by date descending
        all_commits.sort(key=lambda x: x["date"], reverse=True)
        log_info(f"Successfully fetched and sorted {len(all_commits)} total commits.")
        self.send_json_response({"commits": all_commits})

    def handle_plan_worklog(self, data):
        commits = data.get("commits", [])
        gemini_api_key = data.get("gemini_api_key", "").strip()
        start_date_str = data.get("start_date", "")
        end_date_str = data.get("end_date", "")
        leave_dates = [d.strip() for d in data.get("leave_dates", []) if d.strip()]
        half_day_dates = [d.strip() for d in data.get("half_day_dates", []) if d.strip()]
        saturday_work = data.get("saturday_work", False)

        log_info(f"Incoming plan-worklog request. Log Range: {start_date_str} to {end_date_str}")
        log_info(f"Parameters: Leaves={leave_dates}, Half-days={half_day_dates}, Saturday Work={saturday_work}")

        if not start_date_str or not end_date_str:
            log_error("Plan-worklog rejected: Missing start_date or end_date.")
            self.send_error_response(400, "Start date and End date are required for logging.")
            return

        # Calculate working dates in range
        try:
            start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except Exception as e:
            log_error(f"Plan-worklog rejected: Invalid date format: {str(e)}")
            self.send_error_response(400, f"Invalid date format: {str(e)}")
            return

        working_dates = []
        curr = start_date
        while curr <= end_date:
            curr_str = curr.strftime("%Y-%m-%d")
            # Exclude full leaves
            if curr_str in leave_dates:
                curr += datetime.timedelta(days=1)
                continue
            
            # Check weekend
            is_weekend = curr.weekday() in [5, 6] # Saturday, Sunday
            if is_weekend:
                # Include Saturday only if saturday_work is True and it's Saturday
                if curr.weekday() == 5 and saturday_work:
                    working_dates.append(curr_str)
            else:
                working_dates.append(curr_str)
                
            curr += datetime.timedelta(days=1)

        log_info(f"Calculated {len(working_dates)} working dates to log: {working_dates}")

        if not working_dates:
            log_error("Plan-worklog rejected: No working dates in specified range after leave exclusions.")
            self.send_error_response(400, "No working dates found in the specified range with current leaves configuration.")
            return

        # Fallback generator if no Gemini API Key is provided
        if not gemini_api_key:
            log_info("No Gemini API key provided. Executing built-in rule-based fallback planner.")
            plan = self.generate_fallback_plan(commits, working_dates, half_day_dates)
            self.send_json_response({"plan": plan, "mode": "rule_based_fallback"})
            return

        # Prepare Gemini Prompt
        prompt = f"""
You are a developer work log assistant. Your task is to organize a developer's raw Git commits into a structured list of User Stories and nested Tasks, and distribute log hours across working days.

Here are the inputs:
1. Working Dates to log: {json.dumps(working_dates)}
2. Half-Day Dates (max 4.0 hours, other days default to 8.0 or similar): {json.dumps(half_day_dates)}
3. Raw Commits (sorted descending): {json.dumps(commits)}

Instructions:
- Group the work/commits logically into 2 to 4 major "User Stories" (e.g. "Live Tracking Integration", "Daily Assignment Dashboard", "Onboarding Verification").
- Break down each User Story into logical child "Tasks".
- Assign each of the working dates exactly one Task, distributing the logged work chronologically.
- For each day, provide the date, the hours to log (usually 8.0, but exactly 4.0 on half-day dates, and slightly varying like 7.5 or 8.5 on normal days to look realistic), and a user-friendly, professional commit-based work comment describing the task completed.
- Ensure every working date in the list has a logged time entry, and all time entries map to a specific Task.
- Keep descriptions, subject lines, and comments clean and descriptive.

Return ONLY a JSON object matching the following structure:
{{
  "stories": [
    {{
      "story_subject": "User Story Subject Line",
      "story_description": "User Story Description",
      "tasks": [
        {{
          "subject": "Task Subject",
          "description": "Task Description",
          "logs": [
            {{
              "date": "YYYY-MM-DD",
              "hours": 8.0,
              "comment": "Completed the XYZ verification API and database checks"
            }}
          ]
        }}
      ]
    }}
  ]
}}
"""

        # Try to resolve available models dynamically using the user's API key
        available_models = []
        try:
            log_info("Querying available models list for your API key...")
            models_url = f"https://generativelanguage.googleapis.com/v1/models?key={gemini_api_key}"
            req_models = urllib.request.Request(models_url, method="GET")
            with urllib.request.urlopen(req_models, timeout=10) as response_models:
                res_models_body = response_models.read().decode('utf-8')
                res_models_json = json.loads(res_models_body)
                for m in res_models_json.get("models", []):
                    model_name = m.get("name", "")
                    if model_name.startswith("models/"):
                        available_models.append(model_name.replace("models/", ""))
            log_info(f"Dynamically resolved available models: {available_models}")
        except Exception as me:
            log_error(f"Failed to query available models list: {me}")

        # Prioritize candidates from available models list
        candidates = []
        if available_models:
            preferred_order = [
                "gemini-2.5-flash", 
                "gemini-2.0-flash-lite", 
                "gemini-2.0-flash", 
                "gemini-2.5-pro", 
                "gemini-1.5-flash", 
                "gemini-1.5-pro"
            ]
            for p in preferred_order:
                if p in available_models:
                    candidates.append(p)
            for m in available_models:
                if m not in candidates and "gemini" in m and "embedding" not in m and "image" not in m:
                    candidates.append(m)
        if not candidates:
            candidates = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

        log_info(f"Model retry candidates list: {candidates}")

        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }

        plan_data = None
        selected_model = None
        api_error_msg = ""

        for model in candidates:
            log_info(f"Attempting content generation using model: '{model}'...")
            url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={gemini_api_key}"
            try:
                req = urllib.request.Request(
                    url, 
                    data=json.dumps(payload).encode('utf-8'), 
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=30) as response:
                    res_body = response.read().decode('utf-8')
                    res_json = json.loads(res_body)
                    text = res_json['candidates'][0]['content']['parts'][0]['text']
                    log_info(f"Received response from model '{model}'. Size: {len(text)} characters.")
                    
                    # Strip markdown fences if present
                    clean_text = text.strip()
                    if clean_text.startswith("```"):
                        lines = clean_text.split("\n")
                        if lines[0].startswith("```"):
                            lines = lines[1:]
                        if lines and lines[-1].strip() == "```":
                            lines = lines[:-1]
                        clean_text = "\n".join(lines).strip()
                    
                    try:
                        plan_data = json.loads(clean_text)
                        log_info(f"Model '{model}' succeeded and JSON parsed successfully.")
                        selected_model = model
                        break # Success!
                    except json.JSONDecodeError as jde:
                        log_error(f"Failed to parse model '{model}' response as JSON: {jde}")
                        log_error(f"Raw response text:\n{text}")
                        raise jde
            except urllib.error.HTTPError as he:
                err_body = he.read().decode('utf-8') if hasattr(he, 'read') else ""
                log_error(f"Model '{model}' failed with HTTP {he.code}: {he.reason}\nResponse Body: {err_body}")
                api_error_msg = f"HTTP {he.code}: {he.reason} - {err_body}"
            except Exception as e:
                log_error(f"Model '{model}' failed: {e}")
                api_error_msg = str(e)

        if plan_data is not None:
            self.send_json_response({"plan": plan_data.get("stories", []), "mode": "gemini_ai", "model_used": selected_model})
        else:
            log_error("All model candidates failed or returned quota limit errors. Executing rule-based fallback generation.")
            plan = self.generate_fallback_plan(commits, working_dates, half_day_dates)
            self.send_json_response({"plan": plan, "mode": "rule_based_fallback_due_to_api_error", "api_error": api_error_msg})

    def generate_fallback_plan(self, commits, working_dates, half_day_dates):
        # A simple algorithm to split commits and working dates into 3 placeholder User Stories
        num_days = len(working_dates)
        if not commits:
            commits = [{"repo": "workspace", "message": "Development and code refactoring", "hash": "stub"}]

        # Segment dates into 3 buckets
        story_names = [
            "Live Fleet Ingestion & Tracking Service",
            "GPS Device Synchronization & Tenant Onboarding",
            "GTFS Trip Daily and Weekly Assignment Board"
        ]
        story_descs = [
            "Integration of live WebSocket tracking, Redis cache telemetry data, and UI/UX map alignments.",
            "Development of transactional GPS attachment APIs, Surepass KYC checks, and SMS alerts.",
            "Design and implementation of weekly trip scheduling layout, popup assignment selection, and backend GTFS trip management."
        ]

        stories = []
        
        # We distribute the 27 or N days across these 3 stories
        chunk_size = max(1, num_days // 3)
        date_chunks = [working_dates[i:i + chunk_size] for i in range(0, num_days, chunk_size)]
        
        # If there are 4 chunks, merge the last one
        if len(date_chunks) > 3:
            date_chunks[2].extend(date_chunks[3])
            date_chunks = date_chunks[:3]

        for s_idx in range(min(3, len(date_chunks))):
            s_dates = date_chunks[s_idx]
            s_tasks = []
            
            # Create a task for every 1 or 2 days in the story
            task_chunk_size = max(1, len(s_dates) // 3)
            task_dates_list = [s_dates[i:i + task_chunk_size] for i in range(0, len(s_dates), task_chunk_size)]
            
            for t_idx, t_dates in enumerate(task_dates_list):
                # Formulate a task subject from corresponding commits or fallback
                # Find some commits from this time range if possible, otherwise use fallback
                task_subject = f"Feature Phase {t_idx + 1} for {story_names[s_idx].split(' & ')[0]}"
                task_desc = f"Implementation and stabilization of {task_subject.lower()}."
                
                logs = []
                for dt in t_dates:
                    hours = 4.0 if dt in half_day_dates else 8.0
                    # Vary hours slightly to look realistic (e.g. 7.5, 8.5)
                    if hours == 8.0:
                        # simple pattern based on day of month
                        day_num = int(dt.split("-")[-1])
                        if day_num % 3 == 0:
                            hours = 7.5
                        elif day_num % 3 == 1:
                            hours = 8.5
                    
                    # Search a commit message around this date, or grab one sequentially
                    commit_msg = "Implemented components and completed API integration testing"
                    if commits:
                        # find commit nearest or just cyclic
                        c_idx = (working_dates.index(dt)) % len(commits)
                        commit_msg = commits[c_idx]["message"]
                        
                    logs.append({
                        "date": dt,
                        "hours": hours,
                        "comment": commit_msg
                    })
                
                s_tasks.append({
                    "subject": task_subject,
                    "description": task_desc,
                    "logs": logs
                })
                
            stories.append({
                "story_subject": story_names[s_idx],
                "story_description": story_descs[s_idx],
                "tasks": s_tasks
            })
            
        return stories

    def handle_push_redmine(self, data):
        plan = data.get("plan", [])
        redmine_url = data.get("redmine_url", "").strip().rstrip('/')
        redmine_api_key = data.get("redmine_api_key", "").strip()
        project_id = data.get("project_id", 8)

        log_info(f"Incoming push-redmine request. Redmine: {redmine_url}, Project: {project_id}")

        if not plan or not redmine_url or not redmine_api_key:
            log_error("Push-redmine rejected: Missing plan, redmine_url, or redmine_api_key.")
            self.send_error_response(400, "Missing plan, redmine_url, or redmine_api_key.")
            return

        redmine_headers = {
            "X-Redmine-API-Key": redmine_api_key,
            "Content-Type": "application/json"
        }

        results = []

        for story in plan:
            story_subject = story["story_subject"]
            log_info(f"Creating User Story: '{story_subject}'...")
            # 1. Create User Story
            story_payload = {
                "issue": {
                    "project_id": project_id,
                    "tracker_id": 2, # User Story
                    "subject": story_subject,
                    "description": story["story_description"],
                    "assigned_to_id": 90, # Umesh
                    "status_id": 2 # Started
                }
            }
            
            story_id = self.post_to_redmine(f"{redmine_url}/issues.json", redmine_headers, story_payload)
            if not story_id:
                log_error(f"Failed to create User Story: '{story_subject}'")
                results.append({"story": story_subject, "status": "failed", "error": "Failed to create User Story"})
                continue

            log_info(f"Successfully created User Story #{story_id}")

            results_tasks = []
            for task in story.get("tasks", []):
                task_subject = task["subject"]
                log_info(f"Creating Child Task: '{task_subject}' for Parent Story #{story_id}...")
                # 2. Create Task as child of User Story
                task_payload = {
                    "issue": {
                        "project_id": project_id,
                        "tracker_id": 5, # Task
                        "subject": task_subject,
                        "description": task["description"],
                        "parent_issue_id": story_id,
                        "assigned_to_id": 90,
                        "status_id": 2 # Started
                    }
                }
                
                task_id = self.post_to_redmine(f"{redmine_url}/issues.json", redmine_headers, task_payload)
                if not task_id:
                    log_error(f"Failed to create Task: '{task_subject}'")
                    results_tasks.append({"task": task_subject, "status": "failed", "error": "Failed to create Task"})
                    continue

                log_info(f"Successfully created Task #{task_id}")

                results_logs = []
                today_str = datetime.date.today().strftime("%Y-%m-%d")
                for log in task.get("logs", []):
                    log_date = log.get("date", "")
                    if not log_date:
                        continue
                    # Sanitize: if in the future, clamp to today
                    if log_date > today_str:
                        log_info(f"Date {log_date} is in the future. Clamping to today: {today_str}")
                        log_date = today_str

                    log_info(f"Logging {log['hours']} hours on {log_date} for Task #{task_id}...")
                    # 3. Log time
                    log_payload = {
                        "time_entry": {
                            "issue_id": task_id,
                            "spent_on": log_date,
                            "hours": float(log["hours"]),
                            "activity_id": 9, # Development
                            "comments": log["comment"]
                        }
                    }
                    
                    log_ok = self.post_log_to_redmine(f"{redmine_url}/time_entries.json", redmine_headers, log_payload)
                    if log_ok:
                        log_info(f"Successfully logged spent time entry for Task #{task_id} on {log_date}")
                    else:
                        log_error(f"Failed to log spent time entry for Task #{task_id} on {log_date}")
                        
                    results_logs.append({
                        "date": log_date, 
                        "hours": log["hours"], 
                        "status": "success" if log_ok else "failed"
                    })
                    
                results_tasks.append({
                    "task_id": task_id,
                    "task_subject": task_subject,
                    "status": "success",
                    "logs": results_logs
                })
                
            results.append({
                "story_id": story_id,
                "story_subject": story_subject,
                "status": "success",
                "tasks": results_tasks
            })

        self.send_json_response({"results": results})

    def handle_update_status(self, data):
        issue_ids = data.get("issue_ids", [])
        redmine_url = data.get("redmine_url", "").strip().rstrip('/')
        redmine_api_key = data.get("redmine_api_key", "").strip()

        log_info(f"Incoming update-status request for {len(issue_ids)} issues.")

        if not issue_ids or not redmine_url or not redmine_api_key:
            log_error("Update-status rejected: Missing issue_ids, redmine_url, or redmine_api_key.")
            self.send_error_response(400, "Missing issue_ids, redmine_url, or redmine_api_key.")
            return

        redmine_headers = {
            "X-Redmine-API-Key": redmine_api_key,
            "Content-Type": "application/json"
        }

        results = []
        for issue_id in issue_ids:
            log_info(f"Closing Issue #{issue_id} (Setting done to 100%)...")
            payload = {
                "issue": {
                    "status_id": 5, # Closed
                    "done_ratio": 100
                }
            }
            url = f"{redmine_url}/issues/{issue_id}.json"
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode('utf-8'),
                    headers=redmine_headers,
                    method="PUT"
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    log_info(f"Successfully closed Issue #{issue_id}")
                    results.append({"issue_id": issue_id, "status": "success"})
            except Exception as e:
                log_error(f"Failed to close Issue #{issue_id}: {e}")
                results.append({"issue_id": issue_id, "status": "failed", "error": str(e)})

        self.send_json_response({"updates": results})

    def post_to_redmine(self, url, headers, payload):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode('utf-8'),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                res_body = response.read().decode('utf-8')
                res_json = json.loads(res_body)
                return res_json.get("issue", {}).get("id")
        except urllib.error.HTTPError as he:
            err_body = he.read().decode('utf-8') if hasattr(he, 'read') else ""
            log_error(f"Redmine creation HTTP Error {he.code}: {he.reason}\nBody: {err_body}")
            return None
        except Exception as e:
            log_error(f"Redmine creation error: {e}")
            return None

    def post_log_to_redmine(self, url, headers, payload):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode('utf-8'),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status in [200, 201]
        except urllib.error.HTTPError as he:
            err_body = he.read().decode('utf-8') if hasattr(he, 'read') else ""
            log_error(f"Redmine log time HTTP Error {he.code}: {he.reason}\nBody: {err_body}")
            return False
        except Exception as e:
            log_error(f"Redmine log time error: {e}")
            return False

if __name__ == "__main__":
    handler = WorklogHandler
    # Enable socket re-use to avoid port-bind delay on reload
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"Starting server on port {PORT}. Open http://localhost:{PORT}/ in your browser.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
