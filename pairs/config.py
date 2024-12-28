# Configuration settings
PAIRS = [
    {'ticker1': 'MSTR', 'ticker2': 'IBIT'},
    {'ticker1': 'AMAT', 'ticker2': 'LRCX'},
    {'ticker1': 'NVDA', 'ticker2': 'AMD'},
    {'ticker1': 'LCID', 'ticker2': 'RIVN'},
    {'ticker1': 'ENPH', 'ticker2': 'SEDG'},
    {'ticker1': 'NTES', 'ticker2': 'BILI'},
    {'ticker1': 'SLB', 'ticker2': 'HAL'},
    {'ticker1': 'LOW', 'ticker2': 'HD'},
    {'ticker1': 'MAR', 'ticker2': 'HLT'},
    {'ticker1': 'BKNG', 'ticker2': 'EXPE'},
    {'ticker1': 'S', 'ticker2': 'CRWD'},
]

# Trading parameters
DEFAULT_PARAMS = {
    'z_score_window': 45,
    'z_score_threshold': 1.0,
    'correlation_threshold': 0.5,
    'max_position': 1,
    'vol_premium_multiplier': 0.015
}
