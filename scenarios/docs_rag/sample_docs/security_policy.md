# Data Security & Privacy Policy

## Data Protection

- All customer data at rest is encrypted using AES-256; data in transit uses TLS 1.3.
- Personally identifiable information (PII) is never included in application logs.
- Access to production customer data is role-based and logged; every access is
  auditable for a minimum of 7 years per regulatory requirements.

## Fraud Monitoring

- Every transaction is scored in real time by an automated fraud-detection model.
- Transactions flagged as high-risk are held for manual review before settlement,
  and the customer is notified via push notification and SMS.
- Customers can set custom transaction alerts (e.g., "notify me for any purchase over
  $200") from the mobile app.

## Third-Party Data Sharing

- Customer data is never sold to third parties.
- Data is shared with third-party services (e.g., credit bureaus) only as required to
  provide the requested service, and only with the customer's explicit consent where
  required by law.
- Customers can request a full export of their data, or request account deletion,
  from the "Privacy" section of account settings. Deletion requests are processed
  within 30 days, subject to regulatory record-retention requirements.

## Responsible AI

- Models used for credit decisions and churn prediction exclude protected attributes
  (e.g., gender, race) from their feature set.
- Model decisions that affect a customer (e.g., a declined transaction or a credit
  limit change) can always be explained to the customer on request, using the
  underlying model's feature-contribution breakdown.
