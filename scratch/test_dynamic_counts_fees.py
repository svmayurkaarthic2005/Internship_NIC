import asyncio
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Ensure backend can be imported
sys.path.insert(0, os.path.abspath("."))

from backend.database import AsyncSessionLocal
from backend.services.postgres import (
    get_service_register_counts_by_code,
    get_service_code_fees,
    get_all_service_fees
)
from backend.services.chatbot import (
    _all_service_code_comparison_answer,
    _build_service_code_comparison_response,
    _service_code_lookup_answer
)

async def test_dynamic_counts_and_fees():
    async with AsyncSessionLocal() as db:
        # 1. Test live register counts
        counts = await get_service_register_counts_by_code(db)
        print("Dynamic Register Counts retrieved:", len(counts))
        print("0178 count record:", counts.get("0178"))
        assert "0178" in counts
        assert counts["0178"]["total"] == 15
        assert counts["0178"]["approved"] == 8
        assert counts["0178"]["pending"] == 7
        assert counts["0178"]["rejected"] == 0

        # 2. Test fee lookups
        fee_0153 = await get_service_code_fees(db, "0153")
        fee_0154 = await get_service_code_fees(db, "0154")
        all_fees = await get_all_service_fees(db)
        print("0153 fees:", fee_0153)
        print("0154 fees:", fee_0154)
        print("All fees retrieved:", len(all_fees))
        assert fee_0153["govt_fee"] is not None
        assert fee_0153["csc_fee"] is not None
        assert fee_0154["govt_fee"] is not None
        assert fee_0154["csc_fee"] is not None

        # 3. Test _all_service_code_comparison_answer with dynamic DB data
        matrix_output = _all_service_code_comparison_answer(
            "Compare all supported service codes.",
            is_tamil=False,
            dynamic_counts=counts,
            fee_map=all_fees
        )
        print("\n--- MATRIX OUTPUT PREVIEW ---")
        lines = matrix_output.split("\n")
        for line in lines[:25]:
            print(line)
        
        # Verify 0178 has the dynamic count
        assert "15 files in Register (8 appr, 7 pend)" in matrix_output, "Expected 0178 dynamic string not found!"
        # Verify other active codes have live dynamic strings too
        assert "28 files in Register" in matrix_output or "29 files in Register" in matrix_output

        # 4. Test simulate count change to prove dynamism
        mock_counts = dict(counts)
        mock_counts["0178"] = {
            "total": 99,
            "approved": 50,
            "pending": 40,
            "rejected": 9,
            "display": "99 files in Register (50 appr, 40 pend, 9 rej)"
        }
        mock_output = _all_service_code_comparison_answer(
            "Compare all supported service codes.",
            is_tamil=False,
            dynamic_counts=mock_counts,
            fee_map=all_fees
        )
        assert "99 files in Register (50 appr, 40 pend, 9 rej)" in mock_output, "Dynamism test failed: mock count not reflected!"
        print("\n[SUCCESS] Dynamic count behavior verified: string updates when DB/input counts change!")

        # 5. Test pairwise comparison with dynamic fees
        pairwise_resp = _build_service_code_comparison_response(
            ["0153", "0154"],
            "Show only the differences between 0153 and 0154 in a table",
            is_tamil=False,
            fee_overrides=all_fees
        )
        print("\n--- PAIRWISE OUTPUT PREVIEW ---")
        print(pairwise_resp[:600])
        assert fee_0153["govt_fee"] in pairwise_resp
        assert fee_0154["govt_fee"] in pairwise_resp
        print("\n[SUCCESS] Pairwise comparison uses dynamic DB fees!")

        # 6. Test single lookup with dynamic fees
        fee_resp = _service_code_lookup_answer(
            "What is the fee for service code 0154?",
            is_tamil=False,
            brief=False,
            fee_overrides=all_fees
        )
        print("\n--- FEE 0154 RESPONSE PREVIEW ---")
        print(fee_resp)
        assert fee_0154["govt_fee"] in fee_resp
        assert fee_0154["csc_fee"] in fee_resp
        print("\n[SUCCESS] Single service fee lookup uses dynamic DB fees!")

if __name__ == "__main__":
    asyncio.run(test_dynamic_counts_and_fees())
