import subprocess
import time

def test_legacy_script_performance():
    """Measures the execution time of the original crypto_relative_strength.py script."""
    print("\n--- Running Legacy Performance Benchmark ---")
    start_time = time.time()
    
    # Execute the script as a subprocess
    # This is a simple way to run it and measure wall-clock time.
    process = subprocess.run(['python3', 'crypto_relative_strength.py'], capture_output=True, text=True)
    
    end_time = time.time()
    execution_time = end_time - start_time
    
    print(f"Legacy script stdout:\n{process.stdout[-200:]}") # Print last 200 chars of output
    if process.returncode != 0:
        print(f"Legacy script stderr:\n{process.stderr}")
    
    print(f"---> Legacy script execution time: {execution_time:.2f} seconds <---")
    
    # We are not asserting anything here, just recording the time.
    # In a more advanced setup, we could fail the test if it exceeds a threshold.
    assert True

