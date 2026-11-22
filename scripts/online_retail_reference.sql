-- Reference calculation by invoice, independent of the runtime sum/count formula.
WITH invoices AS (
    SELECT DATE_TRUNC('month', invoice_date) AS month, invoice_no,
           SUM(quantity * unit_price) AS amount
    FROM online_retail.invoice_lines
    WHERE country = 'United Kingdom'
      AND invoice_date >= TIMESTAMP '2010-12-01'
      AND invoice_date < TIMESTAMP '2011-12-01'
      AND LEFT(invoice_no, 1) <> 'C'
      AND quantity > 0
      AND unit_price > 0
    GROUP BY 1, 2
)
SELECT TO_CHAR(month, 'YYYY-MM') AS mois, COUNT(*) AS factures,
       SUM(amount) AS montant_gbp, ROUND(AVG(amount), 2) AS panier_gbp
FROM invoices
GROUP BY month
ORDER BY month;
