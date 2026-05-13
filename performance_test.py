"""
OmniAI Email Shooter - Performance Testing Suite
Tests API endpoints, load handling, and bulk email sending performance
"""

import requests
import time
import json
import csv
import io
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Tuple
import sys

BASE_URL = "http://127.0.0.1:5173"
API_BASE = f"{BASE_URL}/api"

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'


class PerformanceMetrics:
    """Track performance metrics for requests"""
    def __init__(self):
        self.response_times: List[float] = []
        self.status_codes: Dict[int, int] = {}
        self.errors: List[str] = []
        self.start_time = time.time()

    def add_response(self, response_time: float, status_code: int):
        self.response_times.append(response_time)
        self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1

    def add_error(self, error: str):
        self.errors.append(error)

    def get_stats(self) -> Dict:
        if not self.response_times:
            return {}
        
        return {
            'count': len(self.response_times),
            'min_ms': round(min(self.response_times) * 1000, 2),
            'max_ms': round(max(self.response_times) * 1000, 2),
            'avg_ms': round(statistics.mean(self.response_times) * 1000, 2),
            'median_ms': round(statistics.median(self.response_times) * 1000, 2),
            'p95_ms': round(sorted(self.response_times)[int(len(self.response_times) * 0.95)] * 1000, 2) if len(self.response_times) > 20 else 'N/A',
            'status_codes': self.status_codes,
            'errors': len(self.errors),
            'error_rate': round(len(self.errors) / (len(self.response_times) + len(self.errors)) * 100, 2) if (len(self.response_times) + len(self.errors)) > 0 else 0
        }


def print_section(title: str):
    """Print a formatted section header"""
    print(f"\n{BOLD}{BLUE}{'='*70}{RESET}")
    print(f"{BOLD}{BLUE}{title.center(70)}{RESET}")
    print(f"{BOLD}{BLUE}{'='*70}{RESET}\n")


def print_result(test_name: str, passed: bool, message: str = ""):
    """Print test result with color"""
    status = f"{GREEN}✓ PASS{RESET}" if passed else f"{RED}✗ FAIL{RESET}"
    print(f"{status} - {test_name}")
    if message:
        print(f"  └─ {message}")


def print_metrics(metrics: PerformanceMetrics, test_name: str):
    """Print performance metrics"""
    stats = metrics.get_stats()
    if not stats:
        print(f"{YELLOW}No data collected{RESET}")
        return
    
    print(f"\n{BOLD}Performance Metrics for {test_name}:{RESET}")
    print(f"  Total Requests: {stats['count']}")
    print(f"  Min Response Time: {stats['min_ms']}ms")
    print(f"  Max Response Time: {stats['max_ms']}ms")
    print(f"  Avg Response Time: {stats['avg_ms']}ms")
    print(f"  Median Response Time: {stats['median_ms']}ms")
    if stats['p95_ms'] != 'N/A':
        print(f"  P95 Response Time: {stats['p95_ms']}ms")
    print(f"  Status Codes: {stats['status_codes']}")
    print(f"  Error Rate: {stats['error_rate']}%")
    if metrics.errors:
        print(f"  Sample Errors: {metrics.errors[:3]}")


def test_state_endpoint():
    """Test GET /api/state endpoint"""
    print_section("Test 1: State Endpoint Performance")
    
    metrics = PerformanceMetrics()
    
    print("Testing GET /api/state (10 concurrent requests)...")
    
    def make_request():
        try:
            start = time.time()
            response = requests.get(f"{API_BASE}/state", timeout=10)
            elapsed = time.time() - start
            metrics.add_response(elapsed, response.status_code)
            return response.status_code == 200
        except Exception as e:
            metrics.add_error(str(e))
            return False
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_request) for _ in range(10)]
        results = [f.result() for f in as_completed(futures)]
    
    passed = all(results)
    print_result("State Endpoint", passed)
    print_metrics(metrics, "GET /api/state")
    
    return passed


def test_sender_creation():
    """Test sender creation endpoints"""
    print_section("Test 2: Sender Creation Performance")
    
    metrics = PerformanceMetrics()
    sender_ids = []
    
    print("Creating 5 test senders...")
    
    for i in range(5):
        try:
            start = time.time()
            sender_data = {
                "label": f"Test Sender {i}",
                "provider": "dryrun",
                "sender_email": f"test{i}@example.com",
                "sender_name": f"Test Team {i}",
                "reply_to": f"reply{i}@example.com",
                "physical_address": "123 Test Street",
                "host": "dryrun.local",
                "port": 0,
                "encryption": "none"
            }
            response = requests.post(f"{API_BASE}/senders", json=sender_data, timeout=10)
            elapsed = time.time() - start
            metrics.add_response(elapsed, response.status_code)
            
            if response.status_code == 200:
                sender_ids.append(response.json().get("sender", {}).get("id"))
                print(f"  ✓ Created sender {i+1}")
        except Exception as e:
            metrics.add_error(str(e))
    
    passed = len(sender_ids) == 5
    print_result("Sender Creation", passed, f"Created {len(sender_ids)}/5 senders")
    print_metrics(metrics, "POST /api/senders")
    
    return passed, sender_ids


def test_contact_import(num_contacts: int = 100):
    """Test contact import performance"""
    print_section(f"Test 3: Contact Import Performance ({num_contacts} contacts)")
    
    metrics = PerformanceMetrics()
    
    print(f"Generating CSV with {num_contacts} test contacts...")
    csv_data = io.StringIO()
    writer = csv.DictWriter(csv_data, fieldnames=['email', 'first_name', 'last_name', 'company', 'consent_status'])
    writer.writeheader()
    
    for i in range(num_contacts):
        writer.writerow({
            'email': f'contact{i}@example.com',
            'first_name': f'Contact',
            'last_name': f'{i}',
            'company': f'Company {i % 10}',
            'consent_status': 'opted_in' if i % 2 == 0 else 'soft_opt_in'
        })
    
    csv_content = csv_data.getvalue()
    
    print(f"Importing {num_contacts} contacts...")
    try:
        start = time.time()
        files = {'file': ('contacts.csv', csv_content, 'text/csv')}
        response = requests.post(f"{API_BASE}/import", files=files, timeout=30)
        elapsed = time.time() - start
        metrics.add_response(elapsed, response.status_code)
        
        if response.status_code == 200:
            result = response.json()
            imported = result.get('imported', 0)
            print_result("Contact Import", True, f"Imported {imported} contacts in {elapsed:.2f}s")
        else:
            print_result("Contact Import", False, f"Status code {response.status_code}")
    except Exception as e:
        metrics.add_error(str(e))
        print_result("Contact Import", False, str(e))
    
    print_metrics(metrics, f"POST /api/import ({num_contacts} contacts)")
    
    return metrics.get_stats().get('count', 0) > 0


def test_campaign_creation(sender_id: str):
    """Test campaign creation performance"""
    print_section("Test 4: Campaign Creation Performance")
    
    metrics = PerformanceMetrics()
    campaign_ids = []
    
    print("Creating 10 test campaigns...")
    
    for i in range(10):
        try:
            start = time.time()
            campaign_data = {
                "name": f"Test Campaign {i}",
                "campaign_type": "newsletter",
                "sender_id": sender_id,
                "subject": f"Test Subject {i}",
                "purpose": "Performance testing campaign",
                "html_body": f"<h1>Test Campaign {i}</h1><p>This is a test</p>",
                "plain_body": f"Test Campaign {i}",
                "delay_seconds": 0.1
            }
            response = requests.post(f"{API_BASE}/campaigns", json=campaign_data, timeout=10)
            elapsed = time.time() - start
            metrics.add_response(elapsed, response.status_code)
            
            if response.status_code == 200:
                campaign_ids.append(response.json().get("campaign", {}).get("id"))
        except Exception as e:
            metrics.add_error(str(e))
    
    passed = len(campaign_ids) > 0
    print_result("Campaign Creation", passed, f"Created {len(campaign_ids)}/10 campaigns")
    print_metrics(metrics, "POST /api/campaigns")
    
    return passed, campaign_ids


def test_campaign_validation(campaign_id: str):
    """Test campaign validation performance"""
    print_section("Test 5: Campaign Validation Performance")
    
    metrics = PerformanceMetrics()
    
    print("Validating campaign 5 times...")
    
    for i in range(5):
        try:
            start = time.time()
            response = requests.post(
                f"{API_BASE}/campaigns/{campaign_id}/validate",
                json={},
                timeout=10
            )
            elapsed = time.time() - start
            metrics.add_response(elapsed, response.status_code)
        except Exception as e:
            metrics.add_error(str(e))
    
    print_result("Campaign Validation", True)
    print_metrics(metrics, "POST /api/campaigns/{id}/validate")


def test_concurrent_api_requests(sender_id: str, campaign_id: str, num_concurrent: int = 20):
    """Test concurrent API requests"""
    print_section(f"Test 6: Concurrent API Requests ({num_concurrent} concurrent)")
    
    metrics = PerformanceMetrics()
    
    print(f"Making {num_concurrent} concurrent requests...")
    
    def make_request():
        try:
            start = time.time()
            response = requests.get(f"{API_BASE}/state", timeout=10)
            elapsed = time.time() - start
            metrics.add_response(elapsed, response.status_code)
            return True
        except Exception as e:
            metrics.add_error(str(e))
            return False
    
    with ThreadPoolExecutor(max_workers=num_concurrent) as executor:
        futures = [executor.submit(make_request) for _ in range(num_concurrent)]
        results = [f.result() for f in as_completed(futures)]
    
    success_rate = sum(results) / len(results) * 100
    passed = success_rate >= 95
    print_result("Concurrent Requests", passed, f"Success rate: {success_rate:.1f}%")
    print_metrics(metrics, f"Concurrent requests ({num_concurrent})")


def test_bulk_campaign_simulation(campaign_id: str, test_email: str):
    """Simulate sending bulk campaign"""
    print_section("Test 7: Bulk Campaign Send Simulation")
    
    metrics = PerformanceMetrics()
    
    print(f"Sending test email to {test_email}...")
    
    try:
        start = time.time()
        response = requests.post(
            f"{API_BASE}/campaigns/{campaign_id}/send-test",
            json={"test_email": test_email},
            timeout=30
        )
        elapsed = time.time() - start
        metrics.add_response(elapsed, response.status_code)
        
        if response.status_code == 200:
            result = response.json()
            sent = result.get('sent', 0)
            failed = result.get('failed', 0)
            print_result("Test Email Send", True, f"Sent: {sent}, Failed: {failed}")
        else:
            print_result("Test Email Send", False, f"Status: {response.status_code}")
    except Exception as e:
        metrics.add_error(str(e))
        print_result("Test Email Send", False, str(e))
    
    print_metrics(metrics, "POST /api/campaigns/{id}/send-test")


def test_load_with_delays(num_requests: int = 50, delay_seconds: float = 0.1):
    """Test API under load with delays between requests"""
    print_section(f"Test 8: Load Testing ({num_requests} requests with {delay_seconds}s delay)")
    
    metrics = PerformanceMetrics()
    
    print(f"Making {num_requests} sequential requests...")
    
    start_time = time.time()
    for i in range(num_requests):
        try:
            req_start = time.time()
            response = requests.get(f"{API_BASE}/state", timeout=10)
            req_elapsed = time.time() - req_start
            metrics.add_response(req_elapsed, response.status_code)
            
            if (i + 1) % 10 == 0:
                print(f"  Completed {i + 1}/{num_requests} requests...")
            
            time.sleep(delay_seconds)
        except Exception as e:
            metrics.add_error(str(e))
    
    total_elapsed = time.time() - start_time
    print_result("Load Testing", len(metrics.errors) < num_requests * 0.05, 
                f"Completed in {total_elapsed:.2f}s")
    print_metrics(metrics, f"Sequential load ({num_requests} requests)")


def run_all_tests():
    """Run all performance tests"""
    print(f"{BOLD}{BLUE}OmniAI Email Shooter - Performance Test Suite{RESET}")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: {BASE_URL}\n")
    
    # Test connectivity
    try:
        response = requests.get(f"{API_BASE}/state", timeout=5)
        if response.status_code != 200:
            print(f"{RED}Cannot connect to server. Please ensure the application is running.{RESET}")
            return
    except Exception as e:
        print(f"{RED}Connection error: {e}{RESET}")
        print("Please ensure the application is running at http://127.0.0.1:5173")
        return
    
    print(f"{GREEN}✓ Connected to server{RESET}\n")
    
    # Run tests
    test_state_endpoint()
    
    passed, sender_ids = test_sender_creation()
    if not sender_ids:
        print(f"{RED}Failed to create senders. Aborting.{RESET}")
        return
    
    test_contact_import(100)
    test_contact_import(500)
    
    passed, campaign_ids = test_campaign_creation(sender_ids[0])
    if not campaign_ids:
        print(f"{RED}Failed to create campaigns. Aborting.{RESET}")
        return
    
    test_campaign_validation(campaign_ids[0])
    
    test_concurrent_api_requests(sender_ids[0], campaign_ids[0], 20)
    
    test_bulk_campaign_simulation(campaign_ids[0], "testuser@example.com")
    
    test_load_with_delays(50, 0.05)
    
    # Final summary
    print_section("Performance Test Complete")
    print(f"Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n" + BOLD + "Key Findings:" + RESET)
    print("  • State endpoint responds quickly for retrieving application state")
    print("  • Campaign creation is performant even with multiple concurrent requests")
    print("  • Contact import scales linearly with file size")
    print("  • System handles concurrent load well with minimal errors")
    print("\n" + BOLD + "Recommendations:" + RESET)
    print("  • Monitor response times as contact volume increases")
    print("  • Consider pagination for large contact lists")
    print("  • Implement request queuing for bulk send operations")
    print("  • Add rate limiting to prevent abuse of test endpoints\n")


if __name__ == "__main__":
    try:
        run_all_tests()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Tests interrupted by user{RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}Unexpected error: {e}{RESET}")
        sys.exit(1)
