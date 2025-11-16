"""
TIMPAL Block Explorer - Static Assets and UI Components
Provides CSS, JavaScript, and HTML components for the enhanced explorer
"""

def get_base_styles(theme="light"):
    """Get base CSS styles with theme support"""
    return f"""
    <style>
        /* Light theme (default) */
        :root {{
            --bg-color: #f5f5f5;
            --card-bg: #ffffff;
            --text-color: #333333;
            --text-secondary: #666666;
            --border-color: #e0e0e0;
            --gradient-start: #667eea;
            --gradient-end: #764ba2;
            --link-color: #2563eb;
            --link-hover: #1e40af;
        }}
        
        /* Dark theme */
        html[data-theme="dark"] {{
            --bg-color: #1a1a1a;
            --card-bg: #2d2d2d;
            --text-color: #e0e0e0;
            --text-secondary: #a0a0a0;
            --border-color: #404040;
            --link-color: #60a5fa;
            --link-hover: #93c5fd;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            background: var(--bg-color);
            color: var(--text-color);
            transition: all 0.3s ease;
        }}
        
        /* General link styling that adapts to theme */
        a {{
            color: var(--link-color);
            text-decoration: none;
            transition: color 0.2s ease;
        }}
        
        a:hover {{
            color: var(--link-hover);
            text-decoration: underline;
        }}
        
        .header {{
            background: linear-gradient(135deg, var(--gradient-start) 0%, var(--gradient-end) 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}
        
        .header h1 {{
            margin: 0 0 10px 0;
            font-size: 2.5em;
        }}
        
        .header p {{
            margin: 0;
            opacity: 0.9;
        }}
        
        .nav {{
            display: flex;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }}
        
        .nav a {{
            color: var(--text-color);
            text-decoration: none;
            padding: 10px 20px;
            background: var(--card-bg);
            border-radius: 5px;
            transition: all 0.2s;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        .nav a:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.15);
        }}
        
        .nav a.active {{
            background: linear-gradient(135deg, var(--gradient-start), var(--gradient-end));
            color: white;
        }}
        
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .stat-card {{
            background: var(--card-bg);
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            transition: all 0.2s;
        }}
        
        .stat-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.15);
        }}
        
        .stat-label {{
            color: var(--text-secondary);
            font-size: 14px;
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .stat-value {{
            font-size: 24px;
            font-weight: bold;
            color: var(--text-color);
        }}
        
        .stat-trend {{
            font-size: 12px;
            color: #10b981;
            margin-top: 5px;
        }}
        
        .stat-trend.down {{
            color: #ef4444;
        }}
        
        .card {{
            background: var(--card-bg);
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        
        .card h2 {{
            margin-top: 0;
            color: var(--text-color);
        }}
        
        .table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        
        .table th {{
            background: var(--bg-color);
            padding: 12px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid var(--border-color);
        }}
        
        .table td {{
            padding: 12px;
            border-bottom: 1px solid var(--border-color);
        }}
        
        .table tr:hover {{
            background: var(--bg-color);
        }}
        
        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }}
        
        .badge-success {{
            background: #10b981;
            color: white;
        }}
        
        .badge-warning {{
            background: #f59e0b;
            color: white;
        }}
        
        .badge-info {{
            background: #3b82f6;
            color: white;
        }}
        
        .badge-secondary {{
            background: #6b7280;
            color: white;
        }}
        
        .monospace {{
            font-family: 'Courier New', monospace;
            background: var(--bg-color);
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.9em;
        }}
        
        .chart-container {{
            position: relative;
            height: 400px;
            margin: 20px 0;
        }}
        
        .network-container {{
            position: relative;
            height: 600px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background: var(--card-bg);
        }}
        
        .theme-toggle {{
            position: fixed;
            top: 20px;
            right: 20px;
            background: var(--card-bg);
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            color: var(--text-color);
            font-size: 1.2em;
            transition: all 0.2s;
        }}
        
        .theme-toggle:hover {{
            transform: scale(1.1);
        }}
        
        .live-indicator {{
            display: inline-block;
            width: 10px;
            height: 10px;
            background: #10b981;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }}
        
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}
        
        .search-box {{
            width: 100%;
            padding: 15px;
            border: 2px solid var(--border-color);
            border-radius: 8px;
            font-size: 16px;
            background: var(--card-bg);
            color: var(--text-color);
            margin-bottom: 20px;
        }}
        
        .search-box:focus {{
            outline: none;
            border-color: var(--gradient-start);
        }}
        
        .grid-2 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }}
        
        @media (max-width: 768px) {{
            .grid-2 {{
                grid-template-columns: 1fr;
            }}
            
            .stats {{
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            }}
            
            .header h1 {{
                font-size: 1.8em;
            }}
        }}
    </style>
    """

def get_chart_js_cdn():
    """Get Chart.js CDN links"""
    return """
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    """

def get_vis_js_cdn():
    """Get Vis.js network CDN links"""
    return """
    <script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/dist/vis-network.min.js"></script>
    <link href="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/dist/vis-network.min.css" rel="stylesheet">
    """

def get_theme_toggle_script():
    """Get theme toggle JavaScript"""
    return """
    <script>
        // Apply theme immediately (before DOM loads to prevent flash)
        (function() {
            const theme = localStorage.getItem('theme') || 'light';
            if (theme === 'dark') {
                document.documentElement.setAttribute('data-theme', 'dark');
            }
        })();
        
        function toggleTheme() {
            const currentTheme = localStorage.getItem('theme') || 'light';
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            localStorage.setItem('theme', newTheme);
            document.documentElement.setAttribute('data-theme', newTheme);
        }
        
        function getTheme() {
            return localStorage.getItem('theme') || 'light';
        }
    </script>
    """

def get_live_updates_script():
    """Get Server-Sent Events (SSE) JavaScript for real-time updates"""
    return """
    <script>
        let eventSource = null;
        
        function startLiveUpdates() {
            if (eventSource) {
                eventSource.close();
            }
            
            eventSource = new EventSource('/stream');
            
            eventSource.onmessage = function(event) {
                const data = JSON.parse(event.data);
                updateStats(data);
            };
            
            eventSource.onerror = function(error) {
                console.error('SSE Error:', error);
                eventSource.close();
                // Reconnect after 5 seconds
                setTimeout(startLiveUpdates, 5000);
            };
        }
        
        function updateStats(data) {
            // Update latest block
            const blockElement = document.getElementById('live-block-height');
            if (blockElement && data.latest_block) {
                blockElement.textContent = '#' + data.latest_block;
            }
            
            // Update total supply
            const supplyElement = document.getElementById('live-total-supply');
            if (supplyElement && data.total_supply_tmpl) {
                supplyElement.textContent = data.total_supply_tmpl;
            }
            
            // Update validator count
            const validatorElement = document.getElementById('live-validator-count');
            if (validatorElement && data.validator_count) {
                validatorElement.textContent = data.validator_count;
            }
            
            // Update transaction count
            const txElement = document.getElementById('live-tx-count');
            if (txElement && data.total_transactions) {
                txElement.textContent = data.total_transactions;
            }
        }
        
        // Start live updates when page loads
        document.addEventListener('DOMContentLoaded', startLiveUpdates);
        
        // Cleanup on page unload
        window.addEventListener('beforeunload', function() {
            if (eventSource) {
                eventSource.close();
            }
        });
    </script>
    """

def get_navigation_html(active_page="home"):
    """Get navigation bar HTML"""
    pages = [
        ("home", "/", "Home"),
        ("blocks", "/blocks", "Blocks"),
        ("transactions", "/transactions", "Transactions"),
        ("send", "/send", "💸 Send"),
        ("validators", "/validators-dashboard", "Validators"),
        ("analytics", "/analytics", "Analytics"),
        ("network", "/network", "Network"),
        ("api", "/api-docs", "API")
    ]
    
    nav_items = []
    for page_id, url, title in pages:
        active_class = " class='active'" if page_id == active_page else ""
        nav_items.append(f'<a href="{url}"{active_class}>{title}</a>')
    
    return f"""
    <div class="nav">
        {' '.join(nav_items)}
    </div>
    """
