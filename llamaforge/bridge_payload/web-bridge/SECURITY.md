# Security

- Never commit `data/config.php`; it contains the owner key and Agent token.
- Use HTTPS in production.
- Keep the `data/` directory blocked from direct web access. Apache rules are included; Nginx requires an equivalent deny rule.
- Browser history isolation is anonymous, device/browser scoped, not an account authentication system.
- A user who clears that browser's local storage loses its Browser Identity and therefore access to its previous anonymous history.
- If the web model controls are exposed publicly, users can request model switches. Restrict the site or disable such controls if that is not desired.
