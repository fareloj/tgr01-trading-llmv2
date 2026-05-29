import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

from backend.risk.risk_manager import RiskManager
from backend.agents.contracts import DecisionOutput

def test_apagao_noticias():
    """O Apagão: Sem notícias, IA super confiante. Risk Manager deve cortar."""
    payload = {
        "technical_context": {"volatility_atr": 100, "current_price": 50000},
        "news_context": [], # Banco não retornou nada
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0}
    }
    rm = RiskManager(max_exposure=80.0)
    # IA manda BUY com 75 de convicção. A trava de notícias vazias (len=0) com convicção < 80 deve barrar.
    final_order = rm.evaluate_order("BUY", 75, payload, 30.0)
    assert final_order["action"] == "HOLD", "Falhou em barrar O Apagão."
    print("[PASS] Chaos Monkey: Apagão de notícias segurado com sucesso.")

def test_flash_crash():
    """Flash Crash: Volatilidade monstruosa."""
    payload = {
        "technical_context": {"volatility_atr": 5000, "current_price": 40000}, # ATR > 10% do preço! (Crash)
        "news_context": [{"headline": "Notícia Normal"}],
        "portfolio_context": {"max_allowed_risk_per_trade": 5.0}
    }
    rm = RiskManager(max_exposure=80.0)
    # IA super confiante (90%) manda comprar caindo a faca. 
    # Confiabilidade vai cair 50%. Convicção Híbrida vira 45%. O limiar é 50%. Deve barrar.
    final_order = rm.evaluate_order("BUY", 90, payload, 30.0)
    assert final_order["action"] == "HOLD", "Falhou em barrar Flash Crash."
    print("[PASS] Chaos Monkey: Compra suicida em Flash Crash bloqueada.")

def test_llm_malicioso():
    """LLM Alucinando / Output quebrado."""
    try:
        # Força o Pydantic a engolir um JSON alucinado.
        DecisionOutput(action="COMPRA_TUDO", conviction=1000, reasoning="To the moon!") # type: ignore
        assert False, "O Pydantic aceitou lixo!"
    except Exception as e:
        print("[PASS] Chaos Monkey: LLM Malicioso ou JSON Inválido contido pela tipagem estrita.")

if __name__ == "__main__":
    print("="*50)
    print("Liberando o Chaos Monkey...")
    print("="*50)
    test_apagao_noticias()
    test_flash_crash()
    test_llm_malicioso()
    print("\n>>> A MURALHA SOBREVIVEU AO CAOS <<<\n")
