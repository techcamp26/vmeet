import random
import string
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

def generate_meeting_id():
    while True:
        part1 = ''.join(random.choices(string.ascii_lowercase, k=3))
        part2 = ''.join(random.choices(string.ascii_lowercase, k=4))
        part3 = ''.join(random.choices(string.ascii_lowercase, k=3))
        m_id = f"{part1}-{part2}-{part3}"
        if not Meeting.objects.filter(meeting_id=m_id).exists():
            return m_id

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar_color = models.CharField(max_length=7, default='#3B82F6')  # Tailwind blue-500 equivalent hex
    bio = models.TextField(blank=True, max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        # Choose a random color for the user avatar
        colors = ['#EF4444', '#F59E0B', '#10B981', '#3B82F6', '#8B5CF6', '#EC4899']
        UserProfile.objects.create(user=instance, avatar_color=random.choice(colors))

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        instance.profile.save()

class Meeting(models.Model):
    meeting_id = models.CharField(max_length=20, unique=True, default=generate_meeting_id)
    title = models.CharField(max_length=100, default="Quick Meeting")
    host = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hosted_meetings')
    password = models.CharField(max_length=128, blank=True, null=True)  # Optional password
    is_waiting_room = models.BooleanField(default=True)
    is_locked = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    scheduled_time = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.meeting_id}) - Host: {self.host.username}"

class MeetingParticipant(models.Model):
    ROLE_CHOICES = [
        ('host', 'Host'),
        ('participant', 'Participant'),
    ]
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meeting_participations')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='participant')
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(blank=True, null=True)
    is_approved = models.BooleanField(default=False)  # True means admitted from waiting room

    def __str__(self):
        return f"{self.user.username} in {self.meeting.meeting_id} ({self.role})"

class ChatMessage(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    recipient = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name='received_private_messages')  # null for group chat
    message = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        to_str = f"Private to {self.recipient.username}" if self.recipient else "Group"
        return f"[{to_str}] {self.sender.username}: {self.message[:20]}"
