import re

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

# Test with the example from the issue
test_str = """
Issued tickets
AIP201-106
Estimate Time: 1,5h
Actual Time: 1h
"""

pattern = r'(?:Est(?:imate)?|Actual)\s*Time:?\s*([0-9]+([.,][0-9]+)?)[hpm]'
times = list(re.finditer(pattern, test_str, re.IGNORECASE))

print(f"Found {len(times)} time matches in the text")

estimate_time = "N/A"
actual_time = "N/A"

for match in times:
    # Extract the full time value including the unit (h, m, p)
    full_match = match.group(0)
    time_value = match.group(1)  # This is just the number part (e.g., "1,5")
    
    # Add the unit back to make it compatible with convert_to_hours
    if 'h' in full_match.lower():
        time_value = time_value + 'h'
    elif 'm' in full_match.lower() or 'p' in full_match.lower():
        time_value = time_value + 'm'
        
    if 'estimate' in full_match.lower():
        estimate_time = time_value
        print(f"Found estimate time: {time_value}")
    elif 'actual' in full_match.lower():
        actual_time = time_value
        print(f"Found actual time: {time_value}")

estimate_hours = convert_to_hours(estimate_time)
actual_hours = convert_to_hours(actual_time)

print(f"\nResults:")
print(f"Extracted estimate_time: {estimate_time}")
print(f"Extracted actual_time: {actual_time}")
print(f"Converted estimate_hours: {estimate_hours}")
print(f"Converted actual_hours: {actual_hours}")
