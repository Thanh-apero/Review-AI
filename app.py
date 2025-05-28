from flask import Flask, render_template, request
from github import Github
import re
from datetime import datetime
import logging
import os
from dotenv import load_dotenv

# Load biến môi trường từ file .env
load_dotenv()

# Cấu hình logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Kiểm tra token GitHub có tồn tại
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN is not set in environment variables")


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


def parse_pr_info(title, body):
    logger.info("=" * 50)
    logger.info("PR PARSING DEBUG")
    logger.info(f"Title: [{title}]")
    logger.info(f"Body: [{body}]")

    # Parse issue number - tìm trong cả title và body
    issue_match = re.search(r'AIP\d+-\d+', title, re.IGNORECASE) or re.search(r'AIP\d+-\d+', body or '', re.IGNORECASE)
    logger.info(f"Issue number pattern match: {issue_match}")
    issue_number = issue_match.group(0).upper() if issue_match else "N/A"  # Chuẩn hóa về dạng viết hoa

    # Parse estimate time từ body
    estimate_pattern = r'Estimate Time:?\s*(\d+[hpm])'
    logger.info(f"Searching for estimate pattern: {estimate_pattern}")
    estimate_match = re.search(estimate_pattern, body or '', re.IGNORECASE)
    logger.info(f"Estimate time match: {estimate_match}")
    estimate_time = estimate_match.group(1) if estimate_match else "N/A"

    # Parse actual time từ body
    actual_pattern = r'Actual Time:?\s*(\d+[hpm])'
    logger.info(f"Searching for actual pattern: {actual_pattern}")
    actual_match = re.search(actual_pattern, body or '', re.IGNORECASE)
    logger.info(f"Actual time match: {actual_match}")
    actual_time = actual_match.group(1) if actual_match else "N/A"

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


@app.route('/', methods=['GET'])
def index():
    try:
        logger.info("Initializing GitHub connection")
        g = Github(GITHUB_TOKEN)
        org = g.get_organization('AperoVN')
        logger.info(f"Connected to organization: {org.login}")

        query = f'org:AperoVN is:pr label:"AI Generate" created:>=2025-04-29'
        logger.info(f"Executing search query: {query}")
        prs = g.search_issues(query=query)

        total_count = prs.totalCount
        logger.info(f"Found {total_count} pull requests")

        prs_data = []
        total_estimate = 0
        total_actual = 0
        developers_stats = {}
        projects_stats = {}

        for pr in prs:
            logger.info(f"\nProcessing PR #{pr.number}")
            parsed_data = parse_pr_info(pr.title, pr.body)

            # Cập nhật thống kê theo developer
            dev = pr.user.login
            if dev not in developers_stats:
                developers_stats[dev] = {
                    'total_prs': 0,
                    'total_estimate': 0,
                    'total_actual': 0
                }
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

        # Chuyển developers set thành list để có thể serialize
        for project in projects_stats:
            projects_stats[project]['developers'] = sorted(list(projects_stats[project]['developers']))

        logger.info(f"Total processed PRs: {len(prs_data)}")

        stats = {
            'total_prs': len(prs_data),
            'total_estimate': round(total_estimate, 2),
            'total_actual': round(total_actual, 2),
            'developers': developers_stats,
            'projects': projects_stats
        }

        return render_template('result.html',
                               prs=prs_data,
                               stats=stats)

    except Exception as e:
        logger.error(f"Error occurred: {str(e)}", exc_info=True)
        return f"Error: {str(e)}"


if __name__ == '__main__':
    # Chạy ứng dụng với cấu hình từ biến môi trường
    debug = os.getenv('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug, host='0.0.0.0', port=int(os.getenv('PORT', '5000')))
