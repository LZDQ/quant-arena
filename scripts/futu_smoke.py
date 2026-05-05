from futu import OpenQuoteContext, SubType

q = OpenQuoteContext(host="127.0.0.1", port=11111)
try:
    print(q.subscribe(["HK.00700"], [SubType.ORDER_BOOK], subscribe_push=False))
    print(q.get_order_book("HK.00700"))
finally:
    q.close()
