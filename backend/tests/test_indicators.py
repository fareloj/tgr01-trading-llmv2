import sys
import pandas as pd
from pathlib import Path

# Adiciona a raiz do projeto no path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

from backend.features.indicators import calculate_technical_status

def test_rsi_overbought():
    """Valida se uma tendência de alta artificial força o RSI acima de 70."""
    data = []
    price = 1000.0
    for i in range(50):
        data.append({
            "timestamp": i,
            "open": price,
            "high": price + 50,
            "low": price,
            "close": price + 50, # Fechamento sempre na máxima
            "volume": 1.0
        })
        price += 50
        
    df = pd.DataFrame(data)
    status = calculate_technical_status(df)
    
    assert status["status"] == "OK"
    assert status["rsi"]["status"] == "OVERBOUGHT", f"Esperado OVERBOUGHT, recebeu {status['rsi']['status']}"
    assert status["rsi"]["value"] >= 90.0, f"RSI deveria ser extremo, mas foi {status['rsi']['value']}"
    print("[PASS] RSI Matemático: Overbought detectado com precisão.")

def test_macd_bearish():
    """Valida se uma queda acentuada reflete num histograma negativo do MACD."""
    data = []
    price = 5000.0
    for i in range(50):
        data.append({
            "timestamp": i,
            "open": price,
            "high": price,
            "low": price - 100,
            "close": price - 100, 
            "volume": 1.0
        })
        price -= 100
        
    df = pd.DataFrame(data)
    status = calculate_technical_status(df)
    
    assert status["status"] == "OK"
    assert status["macd"]["histogram"] < 0, "MACD Histogram deveria ser negativo"
    print("[PASS] MACD Matemático: Queda estrutural detectada perfeitamente.")

def test_insufficient_data():
    """Garante que a falta de dados não seja passada para o LLM."""
    df = pd.DataFrame([{"timestamp": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
    status = calculate_technical_status(df)
    assert status["status"] == "ERROR"
    print("[PASS] Trava de proteção: Bloqueio de dados insuficientes funciona.")

if __name__ == "__main__":
    print("="*50)
    print("Iniciando Bateria de Testes Matemáticos (Sem LLM)")
    print("="*50)
    test_rsi_overbought()
    test_macd_bearish()
    test_insufficient_data()
    print("\n>>> TODOS OS TESTES UNITÁRIOS PASSARAM <<<\n")
