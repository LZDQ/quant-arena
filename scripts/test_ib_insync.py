from ib_insync import IB

ib = IB()
ib.connect("127.0.0.1", 4001, clientId=2)  # live trade
print(ib.managedAccounts())
print(ib.accountSummary())
ib.disconnect()
