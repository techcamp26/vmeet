from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponseForbidden, JsonResponse
from django.db.models import Q
from datetime import datetime


from .models import Meeting, MeetingParticipant, ChatMessage, UserProfile
from .consumers import MeetingConsumer  # To query active meetings in memory

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
        
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        
        if not username or not email or not password:
            messages.error(request, "All fields are required.")
            return render(request, 'auth/register.html')
            
        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, 'auth/register.html')
            
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return render(request, 'auth/register.html')
            
        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already registered.")
            return render(request, 'auth/register.html')
            
        # Create user
        user = User.objects.create_user(username=username, email=email, password=password)
        login(request, user)
        messages.success(request, "Registration successful!")
        return redirect('dashboard')
        
    return render(request, 'auth/register.html')

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
        
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not user.is_active:
                messages.error(request, "Your account has been deactivated/blocked by admin.")
                return render(request, 'auth/login.html')
            login(request, user)
            messages.success(request, f"Welcome back, {username}!")
            return redirect('dashboard')
        else:
            messages.error(request, "Invalid username or password.")
            
    return render(request, 'auth/login.html')

def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect('login')

def forgot_password_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')
        
        try:
            user = User.objects.get(username=username, email=email)
            if new_password != confirm_password:
                messages.error(request, "New passwords do not match.")
                return render(request, 'auth/forgot_password.html')
                
            user.set_password(new_password)
            user.save()
            messages.success(request, "Password reset successful! You can now log in.")
            return redirect('login')
        except User.DoesNotExist:
            messages.error(request, "No matching user found with the provided details.")
            
    return render(request, 'auth/forgot_password.html')

@login_required
def dashboard_view(request):
    user = request.user
    
    # Check if this is POST to create/schedule a meeting or update profile
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'create_instant':
            title = request.POST.get('title', 'Quick Meeting')
            is_waiting = request.POST.get('is_waiting_room') == 'on'
            password = request.POST.get('password', '').strip() or None
            
            meeting = Meeting.objects.create(
                title=title,
                host=user,
                is_waiting_room=is_waiting,
                password=password
            )
            return redirect('room', meeting_id=meeting.meeting_id)
            
        elif action == 'schedule':
            title = request.POST.get('title', 'Scheduled Meeting')
            is_waiting = request.POST.get('is_waiting_room') == 'on'
            password = request.POST.get('password', '').strip() or None
            sched_time_str = request.POST.get('scheduled_time')
            
            if sched_time_str:
                try:
                    # Parse local datetime input
                    sched_time = datetime.strptime(sched_time_str, '%Y-%m-%dT%H:%M')
                    sched_time = timezone.make_aware(sched_time, timezone.get_current_timezone())
                except ValueError:
                    sched_time = timezone.now()
            else:
                sched_time = timezone.now()
                
            meeting = Meeting.objects.create(
                title=title,
                host=user,
                is_waiting_room=is_waiting,
                password=password,
                scheduled_time=sched_time
            )
            messages.success(request, f"Meeting '{title}' scheduled successfully!")
            return redirect('dashboard')
            
        elif action == 'update_profile':
            email = request.POST.get('email')
            bio = request.POST.get('bio', '')
            avatar_color = request.POST.get('avatar_color')
            
            user.email = email
            user.save()
            
            profile = user.profile
            profile.bio = bio
            profile.avatar_color = avatar_color
            profile.save()
            
            messages.success(request, "Profile updated successfully!")
            return redirect('dashboard')

    # Get data for dashboard
    now = timezone.now()
    scheduled_meetings = Meeting.objects.filter(
        host=user, 
        is_active=True, 
        scheduled_time__gt=now
    ).order_by('scheduled_time')

    # History
    # Fetch meetings where they participated
    participated_ids = MeetingParticipant.objects.filter(user=user).values_list('meeting_id', flat=True)
    history_meetings = Meeting.objects.filter(
        Q(host=user) | Q(id__in=participated_ids)
    ).distinct().order_by('-created_at')[:20]

    # Prepopulate standard colors for profile editor
    colors = ['#EF4444', '#F59E0B', '#10B981', '#3B82F6', '#8B5CF6', '#EC4899']

    context = {
        'scheduled_meetings': scheduled_meetings,
        'history_meetings': history_meetings,
        'profile_colors': colors,
        'user_profile': user.profile
    }
    return render(request, 'dashboard.html', context)

@login_required
def room_view(request, meeting_id):
    meeting = get_object_or_404(Meeting, meeting_id=meeting_id)
    
    if not meeting.is_active:
        messages.error(request, "This meeting has already ended.")
        return redirect('dashboard')

    # Verify meeting password if required
    is_host = (meeting.host == request.user)
    session_key = f'meeting_auth_{meeting_id}'
    
    if meeting.password and not is_host:
        if not request.session.get(session_key):
            if request.method == 'POST':
                entered_pass = request.POST.get('meeting_password')
                if entered_pass == meeting.password:
                    request.session[session_key] = True
                else:
                    messages.error(request, "Incorrect meeting password.")
                    return render(request, 'room_password.html', {'meeting': meeting})
            else:
                return render(request, 'room_password.html', {'meeting': meeting})

    context = {
        'meeting': meeting,
        'is_host': is_host,
        'user': request.user,
    }
    return render(request, 'room.html', context)

@login_required
def admin_dashboard_view(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("You do not have access to the Admin Dashboard.")

    if request.method == 'POST':
        action = request.POST.get('action')
        target_user_id = request.POST.get('user_id')
        
        if action == 'toggle_block' and target_user_id:
            target_user = get_object_or_404(User, id=target_user_id)
            if target_user != request.user:  # Do not block self
                target_user.is_active = not target_user.is_active
                target_user.save()
                status = "unblocked" if target_user.is_active else "blocked"
                messages.success(request, f"User {target_user.username} has been {status}.")
            else:
                messages.error(request, "You cannot block yourself.")
            return redirect('admin_dashboard')
            
        elif action == 'admit_user' and target_user_id:
            meeting_id = request.POST.get('meeting_id')
            try:
                meeting = Meeting.objects.get(meeting_id=meeting_id)
                # Update DB
                MeetingParticipant.objects.filter(meeting=meeting, user_id=target_user_id).update(is_approved=True)
                
                # Notify memory socket layers
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                
                channel_layer = get_channel_layer()
                room_group_name = f"meeting_{meeting_id}"
                
                target_chan = None
                peers = MeetingConsumer.active_peers.get(meeting_id, {})
                for ch, info in peers.items():
                    if str(info.get('user_id')) == str(target_user_id):
                        info['approved'] = True
                        target_chan = ch
                        break
                        
                if target_chan:
                    # Notify admitted peer
                    async_to_sync(channel_layer.send)(
                        target_chan,
                        {
                            "type": "admitted_state",
                            "username": peers[target_chan]["username"]
                        }
                    )
                    # Broadcast join to group
                    async_to_sync(channel_layer.group_send)(
                        room_group_name,
                        {
                            "type": "peer_joined",
                            "user_id": int(target_user_id),
                            "username": peers[target_chan]["username"],
                            "channel_name": target_chan
                        }
                    )
                messages.success(request, "Participant admitted successfully!")
            except Exception as e:
                messages.error(request, f"Failed to admit participant: {str(e)}")
            return redirect('admin_dashboard')

    # Calculate real-time active meetings and participant counts from the WebSocket state
    active_meetings_list = []
    total_active_participants = 0
    
    # active_peers looks like: { meeting_id: { channel_name: info_dict } }
    for m_id, peers in MeetingConsumer.active_peers.items():
        try:
            meeting_obj = Meeting.objects.get(meeting_id=m_id)
            # Count approved peers
            active_count = sum(1 for p in peers.values() if p.get('approved', False))
            waiting_count = sum(1 for p in peers.values() if not p.get('approved', False))
            
            active_names = [p.get('username') for p in peers.values() if p.get('approved', False)]
            
            waiting_participants_details = []
            for p in peers.values():
                if not p.get('approved', False):
                    waiting_participants_details.append({
                        'user_id': p.get('user_id'),
                        'username': p.get('username')
                    })
            
            active_meetings_list.append({
                'meeting': meeting_obj,
                'participants_count': active_count,
                'waiting_count': waiting_count,
                'active_names': active_names,
                'waiting_participants_details': waiting_participants_details,
            })
            total_active_participants += active_count
        except Meeting.DoesNotExist:
            pass

    # Standard database analytics
    total_users = User.objects.count()
    total_meetings = Meeting.objects.count()
    completed_meetings = Meeting.objects.filter(is_active=False).count()
    scheduled_count = Meeting.objects.filter(is_active=True, scheduled_time__gt=timezone.now()).count()
    
    users = User.objects.all().order_by('-date_joined')
    recent_meetings = Meeting.objects.all().order_by('-created_at')[:10]

    context = {
        'total_users': total_users,
        'total_meetings': total_meetings,
        'active_meetings_count': len(active_meetings_list),
        'active_meetings': active_meetings_list,
        'total_active_participants': total_active_participants,
        'completed_meetings': completed_meetings,
        'scheduled_count': scheduled_count,
        'users': users,
        'recent_meetings': recent_meetings,
    }
    return render(request, 'admin_dashboard.html', context)
