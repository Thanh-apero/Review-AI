from flask import Flask, render_template, request, jsonify, current_app
from github import Github
from github.PaginatedList import PaginatedList
import re
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from functools import lru_cache
import time
import sys

# Load biến môi trường từ file .env
load_dotenv()

app = Flask(__name__)

# Lấy GitHub token từ biến môi trường
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
# Số lượng PRs tối đa xử lý mỗi lần - mặc định 1000 để lấy tất cả
MAX_PRS = int(os.getenv('MAX_PRS', '1000'))
# Thời gian cache (1 giờ)
CACHE_TIMEOUT = int(os.getenv('CACHE_TIMEOUT', '3600'))
# Thời gian timeout cho GitHub API (60 giây)
GITHUB_TIMEOUT = int(os.getenv('GITHUB_TIMEOUT', '60'))
# Ngày bắt đầu mặc định (30 ngày trước)
DEFAULT_SINCE_DATE = os.getenv('DEFAULT_SINCE_DATE', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))

def get_system_info():
    """Trả về thông tin hệ thống cho templates"""
    return {
        'now': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'github_token': bool(GITHUB_TOKEN),
        'debug': os.getenv('FLASK_DEBUG', '0') == '1'
    }

def convert_to_hours(time_str):
    if not time_str or time_str.strip().lower() == 'n/a':
        return 0

    time_str = time_str.strip().lower()

    # Nếu là phút (30p, 45m)
    minute_match = re.fullmatch(r'(\d+)[mp]', time_str)
    if minute_match:
        return round(float(minute_match.group(1)) / 60, 2)

    # Nếu là giờ với định dạng số thực có đơn vị (1.5h, 1,5h)
    hour_match = re.fullmatch(r'([0-9]+([.,][0-9]+)?)h', time_str)
    if hour_match:
        return float(hour_match.group(1).replace(',', '.'))

    # Nếu là số thực không đơn vị (1.5, 1,5) hoặc số nguyên
    number_match = re.fullmatch(r'([0-9]+([.,][0-9]+)?)', time_str)
    if number_match:
        return float(number_match.group(1).replace(',', '.'))
        
    # Kiểm tra thêm định dạng 'Xh Ym' hoặc 'X,Yh'
    complex_time_match = re.fullmatch(r'([0-9]+)h\s+([0-9]+)[mp]', time_str)
    if complex_time_match:
        hours = float(complex_time_match.group(1))
        minutes = float(complex_time_match.group(2))
        return hours + round(minutes / 60, 2)
        
    # Kiểm tra định dạng '1,5' hoặc '1.5' không có đơn vị
    decimal_match = re.search(r'([0-9]+[.,][0-9]+)', time_str)
    if decimal_match:
        return float(decimal_match.group(1).replace(',', '.'))

    return 0


def get_project_id(issue_number):
    """Trích xuất ID project từ issue number (VD: AIP123-456 -> AIP123)"""
    if issue_number and issue_number != 'N/A':
        match = re.match(r'(AIP\d+)', issue_number, re.IGNORECASE)
        if match:
            return match.group(1).upper()  # Chuẩn hóa về dạng viết hoa
    return 'Unknown'


def parse_pr_info(pr):
    # Parse issue number - tìm trong cả title và body
    issue_match = re.search(r'AIP\d+-\d+', pr.title, re.IGNORECASE) or re.search(r'AIP\d+-\d+', pr.body or '',
                                                                                 re.IGNORECASE)
    issue_number = issue_match.group(0).upper() if issue_match else "N/A"  # Chuẩn hóa về dạng viết hoa

    # Parse estimate time từ body
    time_pattern = r'(?:Est(?:imate)?|Actual)\s*Time:?\s*(\d+[hpm])'
    times = re.finditer(time_pattern, pr.body or '', re.IGNORECASE)

    estimate_time = "N/A"
    actual_time = "N/A"

    for match in times:
        if 'estimate' in match.group(0).lower():
            estimate_time = match.group(1)
        elif 'actual' in match.group(0).lower():
            actual_time = match.group(1)

    # Chuyển đổi thời gian sang giờ
    estimate_hours = convert_to_hours(estimate_time)
    actual_hours = convert_to_hours(actual_time)

    # Lấy project ID
    project_id = get_project_id(issue_number)

    parsed_result = {
        'issue_number': issue_number,
        'project_id': project_id,
        'estimate_time': f"{estimate_hours}h",
        'actual_time': f"{actual_hours}h",
        'estimate_hours': estimate_hours,
        'actual_hours': actual_hours
    }
    return parsed_result

def fetch_and_parse_prs_internal(org_name, label, since_date, until_date=None):
    """Fetch và parse PRs - hàm nội bộ không có cache"""

    try:
        # Thiết lập timeout cho GitHub API
        g = Github(GITHUB_TOKEN, timeout=GITHUB_TIMEOUT)
        org = g.get_organization(org_name)
        
        # Xây dựng query với date range
        query = f'org:{org_name} is:pr'
        
        # Xử lý label (có thể là một hoặc nhiều label)
        if isinstance(label, (list, tuple)) and label:
            # Nếu là danh sách labels - sử dụng phép AND
            label_conditions = [f'label:"{l}"' for l in label if l and l.strip()]
            if label_conditions:
                query += f' {" ".join(label_conditions)}'
        elif label and isinstance(label, str):
            # Nếu là một label duy nhất
            query += f' label:"{label}"'
            
        if since_date:
            query += f' created:>={since_date}'
            
        if until_date:
            query += f' created:<={until_date}'
            
        prs = g.search_issues(query=query)
        # Lấy tổng số PRs thực tế
        actual_total = prs.totalCount
        
        # Nếu MAX_PRS = 0, lấy tất cả PRs
        if MAX_PRS == 0:
            total_count = actual_total
        else:
            total_count = min(actual_total, MAX_PRS)

        prs_data = []
        developers_stats = {}
        projects_stats = {}
        total_estimate = 0
        total_actual = 0

        # Xử lý từng batch PRs với kích thước batch tối ưu
        batch_size = 30  # Tăng kích thước batch để giảm số lần gọi API
        for i in range(0, total_count, batch_size):
            current_batch_size = min(batch_size, total_count - i)
            batch = list(prs[i:min(i + batch_size, total_count)])

            for pr in batch:
                parsed_data = parse_pr_info(pr)

                # Cập nhật thống kê theo developer
                dev = pr.user.login
                if dev not in developers_stats:
                    developers_stats[dev] = {'total_prs': 0, 'total_estimate': 0, 'total_actual': 0}
                developers_stats[dev]['total_prs'] += 1
                developers_stats[dev]['total_estimate'] += parsed_data['estimate_hours']
                developers_stats[dev]['total_actual'] += parsed_data['actual_hours']

                # Cập nhật thống kê theo project
                project = parsed_data['project_id']
                if project not in projects_stats:
                    projects_stats[project] = {
                        'total_prs': 0,
                        'total_estimate': 0,
                        'total_actual': 0,
                        'developers': set()
                    }
                projects_stats[project]['total_prs'] += 1
                projects_stats[project]['total_estimate'] += parsed_data['estimate_hours']
                projects_stats[project]['total_actual'] += parsed_data['actual_hours']
                projects_stats[project]['developers'].add(dev)

                # Cập nhật tổng thời gian
                total_estimate += parsed_data['estimate_hours']
                total_actual += parsed_data['actual_hours']

                pr_info = {
                    'title': pr.title,
                    'issue_number': parsed_data['issue_number'],
                    'estimate_time': parsed_data['estimate_time'],
                    'actual_time': parsed_data['actual_time'],
                    'creator': pr.user.login,
                    'created_at': pr.created_at.strftime('%Y-%m-%d'),
                    'url': pr.html_url
                }
                prs_data.append(pr_info)

        # Chuyển developers set thành list
        for project in projects_stats:
            projects_stats[project]['developers'] = sorted(list(projects_stats[project]['developers']))

        result = {
            'prs_data': prs_data,
            'stats': {
                'total_prs': len(prs_data),
                'total_prs_found': actual_total,  # Thêm tổng số PRs tìm thấy
                'total_prs_processed': total_count,  # Thêm tổng số PRs đã xử lý
                'total_estimate': round(total_estimate, 2),
                'total_actual': round(total_actual, 2),
                'developers': developers_stats,
                'projects': projects_stats
            },
            'timestamp': time.time()
        }
        
        return result

    except Exception as e:
        raise

# Sử dụng cache key tự động tạo từ các tham số
def create_cache_key(org_name, label, since_date, until_date=None):
    # Chuyển label thành chuỗi để có thể hash được
    if isinstance(label, (list, tuple)):
        label_str = ','.join(sorted(label)) if label else 'all'
    else:
        label_str = str(label) if label else 'all'
    
    # Tạo key cho cache
    key = f"{org_name}_{label_str}_{since_date}"
    if until_date:
        key += f"_{until_date}"
    
    return key

# Cache dictionary để lưu kết quả
_pr_cache = {}
_cache_timestamp = {}

def fetch_and_parse_prs(org_name, label, since_date, until_date=None):
    """Fetch và parse PRs với cache tự quản lý"""
    # Tạo cache key
    cache_key = create_cache_key(org_name, label, since_date, until_date)

    # Kiểm tra cache
    current_time = time.time()
    if cache_key in _pr_cache and current_time - _cache_timestamp.get(cache_key, 0) < CACHE_TIMEOUT:
        return _pr_cache[cache_key]
    
    # Nếu không có trong cache hoặc cache đã hết hạn, lấy dữ liệu mới
    result = fetch_and_parse_prs_internal(org_name, label, since_date, until_date)
    
    # Lưu vào cache
    _pr_cache[cache_key] = result
    _cache_timestamp[cache_key] = current_time
    
    return result


@app.route('/health')
def health_check():
    """Endpoint kiểm tra trạng thái ứng dụng"""
    try:
        # Kiểm tra kết nối đến GitHub API
        github_status = 'error'
        error_message = None
        
        if GITHUB_TOKEN:
            try:
                g = Github(GITHUB_TOKEN, timeout=5)  # Timeout ngắn cho health check
                # Thử một API call đơn giản
                _ = g.get_rate_limit()
                github_status = 'connected'
            except Exception as e:
                github_status = 'error'
                error_message = str(e)
        else:
            github_status = 'not_configured'
        
        status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'github_token': 'configured' if GITHUB_TOKEN else 'missing',
            'github_api_status': github_status,
            'github_error': error_message,
            'max_prs': MAX_PRS,
            'cache_timeout': CACHE_TIMEOUT,
            'github_timeout': GITHUB_TIMEOUT,
            'default_since_date': DEFAULT_SINCE_DATE,
            'environment': os.getenv('FLASK_ENV', 'development')
        }
        return jsonify(status)
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/', methods=['GET'])
def index():
    # Lấy tham số từ request hoặc sử dụng giá trị mặc định
    org_name = request.args.get('org', 'AperoVN')
    
    # Xử lý labels (có thể có nhiều label)
    labels = request.args.getlist('label')
    if not labels:
        # Nếu không có label nào được chọn, sử dụng giá trị mặc định
        default_label = request.args.get('default_label', 'AI Generate')
        if default_label:
            labels = [default_label]
    
    since_date = request.args.get('since', DEFAULT_SINCE_DATE)
    until_date = request.args.get('until', '')
    
    # Xử lý yêu cầu
    
    if not GITHUB_TOKEN:
        error_message = "GitHub Token chưa được cấu hình. Vui lòng kiểm tra biến môi trường GITHUB_TOKEN."
        return render_template('error.html', error=error_message, **get_system_info())

    try:
        start_time = time.time()
        
        # Lấy danh sách labels
        available_labels = []
        try:
            available_labels = get_recent_pr_labels(org_name)
        except Exception:
            pass
        
        # Sử dụng cache để lấy dữ liệu
        # Chuyển danh sách labels thành tuple hoặc chuỗi để có thể cache được
        cache_result = fetch_and_parse_prs(org_name, labels, since_date, until_date)
        
        fetch_time = time.time() - start_time

        # Kết hợp dữ liệu với system info
        template_data = {
            **get_system_info(),
            'prs': cache_result['prs_data'],
            'stats': cache_result['stats'],
            'org_name': org_name,
            'labels': labels,  # Danh sách labels đã chọn
            'since_date': since_date,
            'until_date': until_date,
            'available_labels': available_labels,
            'fetch_time': f"{fetch_time:.2f}s"
        }

        return render_template('result.html', **template_data)

    except Exception as e:
        # Chuẩn bị thông báo lỗi chi tiết
        error_type = type(e).__name__
        error_message = f"Lỗi khi xử lý dữ liệu: {error_type}: {str(e)}"
        
        # Thêm thông tin về timeout nếu có thể là lỗi timeout
        if "timeout" in str(e).lower() or "time out" in str(e).lower():
            error_message += f"\n\nLỗi có thể do timeout khi gọi GitHub API. Timeout hiện tại: {GITHUB_TIMEOUT}s"
            error_message += "\nThử tăng giá trị GITHUB_TIMEOUT trong biến môi trường hoặc giảm phạm vi tìm kiếm."
        
        return render_template('error.html', error=error_message, **get_system_info())

# Thêm route để xóa cache khi cần thiết
@app.route('/clear-cache', methods=['GET'])
def clear_cache():
    try:
        global _pr_cache, _cache_timestamp
        _pr_cache = {}
        _cache_timestamp = {}
        return jsonify({
            'status': 'success',
            'message': 'Cache cleared successfully',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error clearing cache: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

# Lấy danh sách labels của một organization
@lru_cache(maxsize=5)
def get_organization_labels(org_name):
    """Lấy danh sách các labels phổ biến trong các repository của organization"""
    try:
        g = Github(GITHUB_TOKEN, timeout=GITHUB_TIMEOUT)
        org = g.get_organization(org_name)
        
        # Lấy các repository của organization
        repos = org.get_repos()[:10]  # Giới hạn số lượng repo để tránh timeout
        
        # Tập hợp để lưu trữ các labels duy nhất
        all_labels = set()
        
        # Lấy labels từ mỗi repository
        for repo in repos:
            try:
                repo_labels = repo.get_labels()
                for label in repo_labels:
                    all_labels.add(label.name)
            except Exception as e:
                print(f"Error fetching labels from repo {repo.name}: {e}")

        # Trả về danh sách labels đã sắp xếp
        return sorted(list(all_labels))
    except Exception:
        return []

# Lấy danh sách labels từ các PRs gần đây
@lru_cache(maxsize=5)
def get_recent_pr_labels(org_name, limit=100):
    """Lấy danh sách các labels từ các PRs gần đây"""
    try:
        g = Github(GITHUB_TOKEN, timeout=GITHUB_TIMEOUT)
        
        # Tìm kiếm các PRs gần đây
        query = f'org:{org_name} is:pr sort:created-desc'
        prs = g.search_issues(query=query)
        
        # Tập hợp để lưu trữ các labels duy nhất
        all_labels = set()
        
        # Lấy labels từ mỗi PR
        count = 0
        for pr in prs:
            if count >= limit:
                break
                
            for label in pr.labels:
                all_labels.add(label.name)
                
            count += 1
        
        # Trả về danh sách labels đã sắp xếp
        return sorted(list(all_labels))
    except Exception:
        return []

# Thêm route để lấy danh sách labels
@app.route('/labels', methods=['GET'])
def get_labels():
    try:
        org_name = request.args.get('org', 'AperoVN')
        labels = get_recent_pr_labels(org_name)
        
        return jsonify({
            'status': 'success',
            'labels': labels,
            'count': len(labels),
            'organization': org_name,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error getting labels: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

# Thêm route để hiển thị cấu hình hiện tại
@app.route('/config', methods=['GET'])
def show_config():
    try:
        config = {
            'max_prs': MAX_PRS,
            'github_timeout': GITHUB_TIMEOUT,
            'cache_timeout': CACHE_TIMEOUT,
            'default_since_date': DEFAULT_SINCE_DATE,
            'environment': os.getenv('FLASK_ENV', 'development'),
            'debug_mode': os.getenv('FLASK_DEBUG', '0') == '1',
            'github_token_configured': bool(GITHUB_TOKEN),
            'python_version': sys.version,
            'timestamp': datetime.now().isoformat()
        }
        return jsonify(config)
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error showing config: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

if __name__ == '__main__':
    # Chạy ứng dụng với cấu hình từ biến môi trường
    port = int(os.getenv('PORT', '5000'))
    debug = os.getenv('FLASK_DEBUG', '0') == '1'

    # Khởi động ứng dụng

    app.run(debug=debug, host='0.0.0.0', port=port)
