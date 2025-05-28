from flask import Flask, render_template, request, jsonify, current_app
from github import Github
from github.PaginatedList import PaginatedList
import re
from datetime import datetime
import logging
import os
from dotenv import load_dotenv
from functools import lru_cache
import time

# Load biến môi trường từ file .env
load_dotenv()

# Cấu hình logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Lấy GitHub token từ biến môi trường
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
# Số lượng PRs tối đa xử lý mỗi lần
MAX_PRS = int(os.getenv('MAX_PRS', '100'))
# Thời gian cache (1 giờ)
CACHE_TIMEOUT = int(os.getenv('CACHE_TIMEOUT', '3600'))

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

    # Nếu là phút (phải kiểm tra trước để tránh hiểu nhầm thành giờ)
    minute_match = re.fullmatch(r'(\d+)[mp]', time_str, re.IGNORECASE)
    if minute_match:
        minutes = float(minute_match.group(1))
        return round(minutes / 60, 2)

    # Nếu là giờ có đơn vị rõ ràng
    hour_match = re.fullmatch(r'(\d+)h', time_str, re.IGNORECASE)
    if hour_match:
        return float(hour_match.group(1))

    # Nếu chỉ là số, mặc định hiểu là giờ
    number_match = re.fullmatch(r'(\d+)', time_str, re.IGNORECASE)
    if number_match:
        return float(number_match.group(1))

    return 0

def get_project_id(issue_number):
    """Trích xuất ID project từ issue number (VD: AIP123-456 -> AIP123)"""
    if issue_number and issue_number != 'N/A':
        match = re.match(r'(AIP\d+)', issue_number, re.IGNORECASE)
        if match:
            return match.group(1).upper()  # Chuẩn hóa về dạng viết hoa
    return 'Unknown'


def parse_pr_info(pr):
    logger.info("=" * 50)
    logger.info("PR PARSING DEBUG")
    logger.info(f"Title: [{pr.title}]")
    logger.info(f"Body: [{pr.body}]")

    # Parse issue number - tìm trong cả title và body
    issue_match = re.search(r'AIP\d+-\d+', pr.title, re.IGNORECASE) or re.search(r'AIP\d+-\d+', pr.body or '',
                                                                                 re.IGNORECASE)
    logger.info(f"Issue number pattern match: {issue_match}")
    issue_number = issue_match.group(0).upper() if issue_match else "N/A"  # Chuẩn hóa về dạng viết hoa

    # Parse estimate time từ body
    time_pattern = r'(?:Estimate|Actual)\s*Time:?\s*(\d+[hpm])'
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
    logger.info(f"Final parsed result: {parsed_result}")
    logger.info("=" * 50)
    return parsed_result


@lru_cache(maxsize=1)
def fetch_and_parse_prs(org_name, label, since_date):
    """Fetch và parse PRs với cache"""
    logger.info(f"Fetching PRs for {org_name} with label {label} since {since_date}")
    cache_key = f"{org_name}_{label}_{since_date}"

    try:
        g = Github(GITHUB_TOKEN)
        org = g.get_organization(org_name)
        query = f'org:{org_name} is:pr label:"{label}" created:>={since_date}'

        prs = g.search_issues(query=query)
        total_count = min(prs.totalCount, MAX_PRS)

        prs_data = []
        developers_stats = {}
        projects_stats = {}
        total_estimate = 0
        total_actual = 0

        # Xử lý từng batch PRs
        batch_size = 20
        for i in range(0, total_count, batch_size):
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

        return {
            'prs_data': prs_data,
            'stats': {
                'total_prs': len(prs_data),
                'total_estimate': round(total_estimate, 2),
                'total_actual': round(total_actual, 2),
                'developers': developers_stats,
                'projects': projects_stats
            },
            'timestamp': time.time()
        }

    except Exception as e:
        logger.error(f"Error fetching PRs: {str(e)}")
        raise


@app.route('/health')
def health_check():
    """Endpoint kiểm tra trạng thái ứng dụng"""
    status = {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'github_token': 'configured' if GITHUB_TOKEN else 'missing',
        'max_prs': MAX_PRS,
        'cache_timeout': CACHE_TIMEOUT
    }
    return jsonify(status)

@app.route('/', methods=['GET'])
def index():
    if not GITHUB_TOKEN:
        error_message = "GitHub Token chưa được cấu hình. Vui lòng kiểm tra biến môi trường GITHUB_TOKEN."
        logger.error(error_message)
        return render_template('error.html', error=error_message, **get_system_info())

    try:
        # Sử dụng cache để lấy dữ liệu
        cache_result = fetch_and_parse_prs('AperoVN', 'AI Generate', '2025-04-29')

        # Kết hợp dữ liệu với system info
        template_data = {
            **get_system_info(),
            'prs': cache_result['prs_data'],
            'stats': cache_result['stats']
        }

        return render_template('result.html', **template_data)

    except Exception as e:
        error_message = f"Lỗi khi xử lý dữ liệu: {str(e)}"
        logger.error(error_message, exc_info=True)
        return render_template('error.html', error=error_message, **get_system_info())

if __name__ == '__main__':
    # Chạy ứng dụng với cấu hình từ biến môi trường
    port = int(os.getenv('PORT', '5000'))
    debug = os.getenv('FLASK_DEBUG', '0') == '1'

    # Log cấu hình khi khởi động
    logger.info(f"Starting application on port {port}")
    logger.info(f"Debug mode: {debug}")
    logger.info(f"GitHub token status: {'configured' if GITHUB_TOKEN else 'missing'}")
    logger.info(f"Max PRs per request: {MAX_PRS}")
    logger.info(f"Cache timeout: {CACHE_TIMEOUT} seconds")

    app.run(debug=debug, host='0.0.0.0', port=port)
