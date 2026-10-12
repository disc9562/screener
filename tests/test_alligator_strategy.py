import pytest
import pandas as pd
from strategy.alligator_strategy import AlligatorStrategy

@pytest.fixture
def alligator_strategy():
    """Returns an instance of AlligatorStrategy with a mock config."""
    config = {
        'use_volume_condition': True, # Default to True for most tests
        'volume_multiplier': 2.5,
        'equity': 100000,
        'risk_percent': 0.05
    }
    return AlligatorStrategy(config=config)

def test_no_signal_generation(alligator_strategy):
    """
    Tests that no signal is generated when conditions are not met.
    """
    # Arrange: Data that should not generate a signal (e.g., a bearish trend)
    data = {
        'Close':  [200, 195, 190, 185, 180, 175, 170, 165, 160, 155],
        'Volume': [100, 110, 100, 120, 100, 90, 80, 70, 60, 50],
    }
    df = pd.DataFrame(data)

    # Act
    signals = alligator_strategy._analyze(df)

    # Assert
    assert isinstance(signals, list)
    assert len(signals) == 0, "A signal was generated when none was expected"

def test_signal_generation_and_content(alligator_strategy):
    """
    Tests that a BUY signal is generated with the correct content when conditions are met.
    """
    # Arrange: Create a dataset where a BUY signal is expected at the last row
    data = {
        'Close':  [180, 185, 190, 195, 200, 205, 210, 215, 220, 225],
        'Volume': [100, 110, 120, 130, 140, 150, 160, 170, 100, 500] # Volume spike at the end
    }
    df = pd.DataFrame(data)
    
    # Act
    signals = alligator_strategy._analyze(df.copy())
    
    # Assert
    # 1. Check return type and that a signal was generated
    assert isinstance(signals, list)
    assert len(signals) > 0, "No BUY signal was generated, check test data and logic"
    
    # 2. Check the content of the first signal
    signal = signals[0]
    expected_keys = ['timestamp', 'signal', 'entry_price', 'stop_loss', 'take_profit', 'units']
    for key in expected_keys:
        assert key in signal

    assert signal['signal'] == 'BUY'
    assert signal['entry_price'] == 225 # From the last row of test data
    
    # 3. Verify position size calculation
    config = alligator_strategy.config
    equity = config.get('equity')
    risk_percent = config.get('risk_percent')
    offset = signal['entry_price'] - signal['stop_loss']
    
    # Ensure offset is not zero to avoid division by zero error
    assert offset > 0
    
    expected_units = (equity * risk_percent) / offset
    
    assert signal['units'] == pytest.approx(expected_units)

def test_volume_condition_off_no_signal(alligator_strategy):
    """
    Tests that no signal is generated when volume condition is OFF, even if other conditions are met.
    """
    # Arrange: Data that should NOT generate a signal even if volume condition is OFF
    # Create a dataset where base conditions are NOT met (e.g., a downtrend)
    data = {
        'Close':  [225, 220, 215, 210, 205, 200, 195, 190, 185, 180],
        'Volume': [100, 110, 120, 130, 140, 150, 160, 170, 180, 190] # Volume doesn't matter here
    }
    df = pd.DataFrame(data)

    # Set use_volume_condition to False in the strategy config
    alligator_strategy.config['use_volume_condition'] = False

    # Act
    signals = alligator_strategy._analyze(df.copy())

    # Assert
    assert isinstance(signals, list)
    assert len(signals) == 0, "A signal was generated when volume condition was OFF and not expected"

def test_volume_condition_off_signal_generated(alligator_strategy):
    """
    Tests that a signal is generated when volume condition is OFF, and base conditions are met.
    """
    # Arrange: Data where base conditions are met, and volume condition is OFF
    data = {
        'Close':  [180, 185, 190, 195, 200, 205, 210, 215, 220, 225],
        'Volume': [100, 110, 120, 130, 140, 150, 160, 170, 180, 190] # Volume doesn't matter here
    }
    df = pd.DataFrame(data)

    # Set use_volume_condition to False in the strategy config
    alligator_strategy.config['use_volume_condition'] = False

    # Act
    signals = alligator_strategy._analyze(df.copy())

    # Assert
    assert isinstance(signals, list)
    assert len(signals) > 0, "No BUY signal was generated when volume condition was OFF and expected"
    signal = signals[0]
    assert signal['signal'] == 'BUY'
    assert signal['entry_price'] == 225
