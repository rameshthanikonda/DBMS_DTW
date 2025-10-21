document.addEventListener('DOMContentLoaded', () => {
    // --- PASSWORD TOGGLE LOGIC ---
    // This finds ALL toggle icons on the page
    const passwordToggles = document.querySelectorAll('.toggle-password');

    // It gives the click functionality to EACH icon
    passwordToggles.forEach(toggle => {
        toggle.addEventListener('click', (e) => {
            e.preventDefault();
            // Robustly find the input in the same wrapper
            const wrapper = toggle.closest('.password-wrapper');
            if (!wrapper) return;
            const passwordInput = wrapper.querySelector('input[type="password"], input[type="text"]');
            if (!passwordInput) return;

            // Toggle the input type
            const isPassword = passwordInput.getAttribute('type') === 'password';
            passwordInput.setAttribute('type', isPassword ? 'text' : 'password');

            // Toggle the icon appearance
            toggle.classList.toggle('fa-eye', !isPassword);
            toggle.classList.toggle('fa-eye-slash', isPassword);

            // Accessibility
            toggle.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
            toggle.setAttribute('title', isPassword ? 'Hide password' : 'Show password');
        });
    });

    // --- NOTIFICATION PANEL LOGIC (Add this part) ---
    const notificationIcon = document.getElementById('notification-icon');
    const notificationPanel = document.getElementById('notification-panel');
    const notificationBadge = document.querySelector('.notification-badge');

    if (notificationIcon) {
        notificationIcon.addEventListener('click', (event) => {
            event.preventDefault();
            // Toggle panel visibility
            if (!notificationPanel) return;
            const isVisible = notificationPanel.style.display === 'block';
            notificationPanel.style.display = isVisible ? 'none' : 'block';

            // If opening the panel, fetch notifications
            if (!isVisible) {
                fetchNotifications();
            }
        });
    }

    // Close panel if clicking outside of it
    document.addEventListener('click', (event) => {
        if (notificationIcon && notificationPanel && !notificationIcon.contains(event.target) && !notificationPanel.contains(event.target)) {
            notificationPanel.style.display = 'none';
        }
    });

    async function fetchNotifications() {
        try {
            const response = await fetch('/get_notifications');
            if (!response.ok) throw new Error('Failed to fetch notifications');

            const notifications = await response.json();

            let panelContent = '<div class="notification-panel-header">Notifications</div>';
            if (notifications.length === 0) {
                panelContent += '<div class="notification-empty">You have no new notifications.</div>';
            } else {
                panelContent += '<ul class="notification-list">';
                notifications.forEach(notif => {
                    const itemClass = notif.STATUS.toLowerCase() === 'unread' ? 'notification-item unread' : 'notification-item';
                    // Use CREATED_AT from the corrected app.py which is a string
                    const date = new Date(notif.CREATED_AT).toLocaleDateString("en-US", { month: 'long', day: 'numeric' });
                    panelContent += `<li class="${itemClass}">
                        ${notif.MESSAGE}
                        <small>${date}</small>
                    </li>`;
                });
                panelContent += '</ul>';
            }

            notificationPanel.innerHTML = panelContent;

            // If there was a badge, mark notifications as read on the backend
            if (notificationBadge) {
                markAsRead();
            }
        } catch (error) {
            notificationPanel.innerHTML = '<div class="notification-empty">Could not load notifications.</div>';
            console.error(error);
        }
    }

    async function markAsRead() {
        try {
            await fetch('/mark_notifications_read', { method: 'POST' });
            // Hide the badge visually after marking as read
            if(notificationBadge) {
                notificationBadge.style.display = 'none';
            }
        } catch (error) {
            console.error('Failed to mark notifications as read:', error);
        }
    }

    // --- AUTO-DISMISS SUCCESS FLASHES (e.g., login/register/admin login) ---
    const successFlashes = document.querySelectorAll('.flash.success');
    if (successFlashes.length > 0) {
        setTimeout(() => {
            successFlashes.forEach(el => {
                el.classList.add('hide');
                // Fully remove from DOM after fade
                setTimeout(() => el.remove(), 300);
            });
        }, 1000); // 1 second visible
    }
});