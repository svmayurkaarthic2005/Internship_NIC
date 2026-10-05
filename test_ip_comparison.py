"""get_ip_comparison() across every service code, not just ISD/NISD (0153/0154).

Runtime/deterministic-handler check -- not LoRA training data, per the held-out-set review:
IP comparison is a PostgreSQL-computed answer (applications.submission_ip /
service_register_entries.submission_ip), so it is tested here against the live DB, not taught
to the model.

    python test_ip_comparison.py
"""
import asyncio

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer, ServiceRegisterEntry
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.postgres import get_ip_comparison


async def officer_ctx(db, officer):
    jur = await get_officer_jurisdiction_ids(officer.id, db)
    ids = jur["district_ids"] + jur["taluk_ids"] + jur["town_ids"] + jur["ward_ids"] + jur["block_ids"]
    return OfficerContext(officer_id=officer.id, employee_id=officer.employee_id, name=officer.name,
                           email=officer.email, designation=officer.designation,
                           jurisdiction_type=jur["jurisdiction_type"], jurisdiction_name=jur["jurisdiction_name"],
                           jurisdiction_ids=[i for i in ids if i])


async def main():
    async with AsyncSessionLocal() as db:
        officer = (await db.execute(select(SISOfficer))).scalars().first()
        assert officer, "no seeded officer found -- run the sample_db_new build first"
        ctx = await officer_ctx(db, officer)

        non_isd_nisd = (await db.execute(
            select(ServiceRegisterEntry.application_number, ServiceRegisterEntry.submission_ip,
                   ServiceRegisterEntry.service_code)
            .where(ServiceRegisterEntry.service_code.notin_(["0153", "0154"]),
                   ServiceRegisterEntry.submission_ip.isnot(None),
                   ServiceRegisterEntry.ward_id.in_(ctx.jurisdiction_ids or [])))).all()
        assert non_isd_nisd, "no non-ISD/NISD entry with an IP in this officer's jurisdiction -- rebuild the DB"
        codes_seen = {c for _, _, c in non_isd_nisd}
        print(f"non-ISD/NISD entries with an IP: {len(non_isd_nisd)}, service codes: {sorted(codes_seen)}")

        by_ip = {}
        for num, ip, code in non_isd_nisd:
            by_ip.setdefault(ip, []).append(num)
        same_ip_pair = next((apps[:2] for apps in by_ip.values() if len(apps) >= 2), None)
        assert same_ip_pair, "no two non-ISD/NISD applications share an IP in the sample -- nothing to assert a match on"

        res = await get_ip_comparison(db, ctx, same_ip_pair)
        assert res["count"] == 2, f"expected both applications to resolve, got {res}"
        assert len(res["groups"]) == 1 and len(res["groups"][0][1]) == 2, f"expected one shared-IP group of 2, got {res}"
        print(f"MATCH  {same_ip_pair} -> {res['groups']}")

        diff_ip_apps = [num for ips in by_ip.values() if len(ips) == 1 for num in ips]
        if len(diff_ip_apps) >= 2:
            a, b = diff_ip_apps[0], diff_ip_apps[1]
            res2 = await get_ip_comparison(db, ctx, [a, b])
            assert not res2["groups"], f"expected no shared-IP group for different IPs, got {res2}"
            assert len(res2["singles"]) == 2, f"expected both as singles, got {res2}"
            print(f"DIFFER {[a, b]} -> {res2['singles']}")

        # the whole point: a code outside 0153/0154 must not be silently dropped as "not found"
        one_code_num = non_isd_nisd[0][0]
        res3 = await get_ip_comparison(db, ctx, [one_code_num])
        assert res3["dropped"] == 0, f"a real application outside ISD/NISD was dropped: {res3}"
        print(f"single non-ISD/NISD application resolves: {one_code_num} -> dropped={res3['dropped']}")

    print("\nAll IP-comparison checks passed across service codes:", sorted(codes_seen))


if __name__ == "__main__":
    asyncio.run(main())
