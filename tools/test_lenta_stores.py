"""Quick test: which Lenta storeId format works for product search."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.connectors import mcp_client

# Moscow DY=1537, HM=291; SPB addr1 DY=1516 HM=13; SPB addr2 DY=1516 HM=268
for label, store_id in [("MSK DY", 1537), ("MSK HM", 291),
                         ("SPB1 DY", 1516), ("SPB1 HM", 13),
                         ("SPB2 DY", 1516), ("SPB2 HM", 268)]:
    result = mcp_client.call_tool(
        "https://mcp.lenta.com/mcp", "lenta",
        "storefront_products_search",
        {"query": "молоко", "storeId": store_id, "channel": "lo", "limit": 3}
    )
    payload = mcp_client.ok_payload(result)
    if payload:
        items = payload.get("items", [])
        print(f"{label} storeId={store_id}: {len(items)} items")
        for it in items[:2]:
            name = (it.get("name") or "?")[:40]
            price = it.get("price")
            stock = it.get("stock")
            print(f"  {name}  price={price}  stock={stock}")
    else:
        print(f"{label} storeId={store_id}: empty/error")
    print()
