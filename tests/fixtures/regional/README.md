# Reference report city-day fixture

`zhengzhou-history-20260918.json` is the unchanged public CNEMC city-day response used by the approved reference report, retrieved 2026-09-18. City code 410100, dates 2026-09-04 through 2026-09-17.

Source: https://air.cnemc.cn:18007/HourChangesPublish/GetCityDayAqiHistoryByCondition?citycode=410100 (POST)
SHA256: fbb0c33959048117d99753b2f118390387e0151159ff908d7eb3dfd7b483951a

Regression checks reconcile published AQI with all six IAQI values and reproduce the reference's 47.9% ozone share. This fixture is not loaded as current service observations.
