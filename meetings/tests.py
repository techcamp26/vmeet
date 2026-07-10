from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from meetings.models import Meeting, UserProfile, MeetingParticipant, ChatMessage

class AuthenticationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.username = 'testuser'
        self.email = 'testuser@example.com'
        self.password = 'testpass123'
        
    def test_user_registration(self):
        # Register user
        response = self.client.post(reverse('register'), {
            'username': self.username,
            'email': self.email,
            'password': self.password,
            'confirm_password': self.password
        })
        self.assertEqual(response.status_code, 302)  # Redirects to dashboard
        self.assertTrue(User.objects.filter(username=self.username).exists())
        
        # Verify user profile creation signal
        user = User.objects.get(username=self.username)
        self.assertTrue(hasattr(user, 'profile'))
        self.assertIsNotNone(user.profile.avatar_color)

    def test_user_login(self):
        # Create user
        User.objects.create_user(username=self.username, password=self.password)
        
        # Login
        response = self.client.post(reverse('login'), {
            'username': self.username,
            'password': self.password
        })
        self.assertEqual(response.status_code, 302)  # Redirects to dashboard

class MeetingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.host = User.objects.create_user(username='hostuser', password='password123')
        self.participant = User.objects.create_user(username='joinuser', password='password123')
        
    def test_meeting_id_generation(self):
        meeting = Meeting.objects.create(title="Sprint Sync", host=self.host)
        # Matches format: xxx-xxxx-xxx
        self.assertEqual(len(meeting.meeting_id), 12)
        self.assertEqual(meeting.meeting_id[3], '-')
        self.assertEqual(meeting.meeting_id[8], '-')

    def test_create_instant_meeting_post(self):
        self.client.login(username='hostuser', password='password123')
        response = self.client.post(reverse('dashboard'), {
            'action': 'create_instant',
            'title': 'Test Instant Meet',
            'is_waiting_room': 'on',
            'password': '123'
        })
        # Check redirect to room
        self.assertEqual(response.status_code, 302)
        # Verify created
        meeting = Meeting.objects.filter(host=self.host).last()
        self.assertEqual(meeting.title, 'Test Instant Meet')
        self.assertEqual(meeting.password, '123')
        self.assertTrue(meeting.is_waiting_room)

    def test_schedule_meeting_post(self):
        self.client.login(username='hostuser', password='password123')
        scheduled_time = (timezone.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(reverse('dashboard'), {
            'action': 'schedule',
            'title': 'Test Scheduled Meet',
            'scheduled_time': scheduled_time,
            'password': ''
        })
        self.assertEqual(response.status_code, 302)
        # Verify created
        meeting = Meeting.objects.filter(title='Test Scheduled Meet').last()
        self.assertIsNotNone(meeting.scheduled_time)
        self.assertFalse(meeting.password)

class ViewAccessControlTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.regular_user = User.objects.create_user(username='regular', password='password123')
        self.staff_user = User.objects.create_user(username='staff', password='password123', is_staff=True)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)  # Redirects to login

    def test_admin_dashboard_staff_only(self):
        # Try as regular user -> 403
        self.client.login(username='regular', password='password123')
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 403)
        
        # Try as staff -> 200
        self.client.login(username='staff', password='password123')
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)
