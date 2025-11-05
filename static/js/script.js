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

    // --- Profile: inline name edit/save ---
    const editableName = document.getElementById('editable-name');
    if (editableName) {
        const saveName = async () => {
            const full_name = editableName.textContent.trim();
            if (!full_name) { return; }
            const prev = editableName.getAttribute('data-prev') || editableName.textContent;
            try {
                const res = await fetch('/profile/name', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ full_name })
                });
                if (!res.ok) throw new Error('Failed to save name');
                const data = await res.json();
                if (!data.success) throw new Error(data.error || 'Failed to save name');
                editableName.setAttribute('data-prev', data.full_name);
            } catch (e) {
                // Revert on error
                editableName.textContent = prev;
                alert('Could not save name. Please try again.');
            }
        };
        editableName.setAttribute('data-prev', editableName.textContent.trim());
        editableName.addEventListener('blur', () => {
            // Only save if we were in edit mode
            if (editableName.getAttribute('contenteditable') === 'true') {
                saveName();
                editableName.setAttribute('contenteditable', 'false');
                if (editBtn && saveBtn) { editBtn.style.display = ''; saveBtn.style.display = 'none'; }
            }
        });
        editableName.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                editableName.blur();
            }
        });

        const editBtn = document.getElementById('edit-name-btn');
        const saveBtn = document.getElementById('save-name-btn');
        if (editBtn && saveBtn) {
            editBtn.addEventListener('click', () => {
                editableName.setAttribute('contenteditable', 'true');
                editBtn.style.display = 'none';
                saveBtn.style.display = '';
                // place cursor at end
                const range = document.createRange();
                range.selectNodeContents(editableName);
                range.collapse(false);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                editableName.focus();
            });
            saveBtn.addEventListener('click', () => {
                editableName.blur();
            });
        }
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
                    const date = new Date(notif.CREATED_AT).toLocaleDateString("en-US", { month: 'long', day: 'numeric' });
                    const msg = notif.MESSAGE || '';
                    let tagClass = 'notif-generic';
                    let tagLabel = 'Notice';
                    const lower = msg.toLowerCase();
                    if (lower.includes('has expired')) {
                        tagClass = 'notif-expired';
                        tagLabel = 'Expired';
                    } else if (lower.includes('expires on') || lower.includes('expiring')) {
                        tagClass = 'notif-expiring';
                        tagLabel = 'Expiring';
                    }
                    panelContent += `<li class="${itemClass}">
                        <div class="notif-title">${msg}</div>
                        <div>
                            <span class="notif-tag ${tagClass}">${tagLabel}</span>
                            <small class="notif-date">${date}</small>
                        </div>
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

    // --- Profile: toggle change password form ---
    const cpToggle = document.getElementById('change-password-toggle');
    const cpForm = document.getElementById('change-password-form');
    if (cpToggle && cpForm) {
        cpToggle.addEventListener('click', () => {
            const visible = cpForm.style.display === 'block';
            cpForm.style.display = visible ? 'none' : 'block';
        });
    }
});