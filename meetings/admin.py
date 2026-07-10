from django.contrib import admin
from .models import UserProfile, Meeting, MeetingParticipant, ChatMessage

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'avatar_color', 'created_at')
    search_fields = ('user__username', 'user__email')

@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ('meeting_id', 'title', 'host', 'is_waiting_room', 'is_locked', 'is_active', 'scheduled_time', 'created_at')
    list_filter = ('is_active', 'is_waiting_room', 'is_locked')
    search_fields = ('meeting_id', 'title', 'host__username')

@admin.register(MeetingParticipant)
class MeetingParticipantAdmin(admin.ModelAdmin):
    list_display = ('meeting', 'user', 'role', 'joined_at', 'left_at', 'is_approved')
    list_filter = ('role', 'is_approved')
    search_fields = ('meeting__meeting_id', 'user__username')

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('meeting', 'sender', 'recipient', 'message', 'timestamp')
    search_fields = ('meeting__meeting_id', 'sender__username', 'message')

