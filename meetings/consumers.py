import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from .models import Meeting, MeetingParticipant, ChatMessage

class MeetingConsumer(AsyncJsonWebsocketConsumer):
    # Store active connections in a class-level dictionary to manage individual routing
    # Format: { meeting_id: { channel_name: { "user_id": id, "username": name, "role": role, "approved": bool } } }
    active_peers = {}

    async def connect(self):
        self.meeting_id = self.scope['url_route']['kwargs']['meeting_id']
        self.room_group_name = f"meeting_{self.meeting_id}"
        self.user = self.scope['user']

        if not self.user.is_authenticated:
            await self.close(code=4001)  # Unauthorized
            return

        # Fetch meeting details from database
        self.meeting = await self.get_meeting_db(self.meeting_id)
        if not self.meeting or not self.meeting.is_active:
            await self.close(code=4002)  # Meeting not found or ended
            return

        self.host = await self.get_meeting_host_db(self.meeting)
        self.is_host = (self.user.id == self.host.id)

        # Handle locked meeting
        if self.meeting.is_locked and not self.is_host:
            await self.close(code=4003)  # Meeting locked
            return

        # Initialize active_peers entry for this meeting if not exists
        if self.meeting_id not in MeetingConsumer.active_peers:
            MeetingConsumer.active_peers[self.meeting_id] = {}

        # Determine if participant starts as approved
        # Host is always approved. If waiting room is disabled, user is approved.
        self.is_approved = self.is_host or not self.meeting.is_waiting_room

        # Register self in active peers
        MeetingConsumer.active_peers[self.meeting_id][self.channel_name] = {
            "user_id": self.user.id,
            "username": self.user.username,
            "role": "host" if self.is_host else "participant",
            "approved": self.is_approved
        }

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

        if self.is_host:
            # Let host know they are connected and list existing peers
            await self.send_json({
                "type": "connection_success",
                "role": "host",
                "username": self.user.username,
                "is_approved": True
            })
            # Also register the participant in the database
            await self.register_participant_db(self.meeting, self.user, 'host', True)
            
            # Send list of any waiting or already joined peers (mostly relevant on reconnects)
            await self.broadcast_peer_list_to_host()
        else:
            # Register participant in db (approved or not)
            await self.register_participant_db(self.meeting, self.user, 'participant', self.is_approved)

            if not self.is_approved:
                # Put in waiting state
                await self.send_json({
                    "type": "waiting_state",
                    "username": self.user.username
                })
                # Notify the host that a peer is waiting
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "waiting_room_request",
                        "user_id": self.user.id,
                        "username": self.user.username,
                        "channel_name": self.channel_name
                    }
                )
            else:
                # Connect directly
                await self.send_json({
                    "type": "connection_success",
                    "role": "participant",
                    "username": self.user.username,
                    "is_approved": True
                })
                # Broadcast to group that a new peer joined to establish WebRTC connections
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "peer_joined",
                        "user_id": self.user.id,
                        "username": self.user.username,
                        "channel_name": self.channel_name
                    }
                )

    async def disconnect(self, close_code):
        if hasattr(self, 'meeting_id') and self.meeting_id in MeetingConsumer.active_peers:
            # Remove peer from active dictionary
            if self.channel_name in MeetingConsumer.active_peers[self.meeting_id]:
                del MeetingConsumer.active_peers[self.meeting_id][self.channel_name]
            
            # If no more peers left, remove the meeting key
            if not MeetingConsumer.active_peers[self.meeting_id]:
                del MeetingConsumer.active_peers[self.meeting_id]

        if hasattr(self, 'room_group_name'):
            # Set left_at in db
            await self.mark_participant_left_db(self.meeting, self.user)

            # Broadcast that peer left
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "peer_left",
                    "user_id": self.user.id,
                    "username": self.user.username,
                    "channel_name": self.channel_name
                }
            )

            # Leave group
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive_json(self, content):
        msg_type = content.get('type')
        target_channel = content.get('target_channel')

        # WebRTC Signaling (SDP Offer, SDP Answer, ICE Candidates)
        if msg_type in ['sdp-offer', 'sdp-answer', 'ice-candidate']:
            if target_channel:
                await self.channel_layer.send(
                    target_channel,
                    {
                        "type": "signal_message",
                        "sender_channel": self.channel_name,
                        "sender_username": self.scope['user'].username,
                        "sender_id": self.scope['user'].id,
                        "payload": content
                    }
                )
            return

        # Real-time Chat
        if msg_type == 'chat_message':
            message_text = content.get('message')
            recipient_username = content.get('recipient')  # username or None (for group)

            if not message_text:
                return

            if recipient_username:
                # Private chat
                db_msg = await self.save_chat_message_db(self.meeting_id, self.user, message_text, recipient_username)
                
                # Find recipient channel
                recipient_channels = [
                    ch for ch, info in MeetingConsumer.active_peers.get(self.meeting_id, {}).items()
                    if info['username'] == recipient_username
                ]
                
                # Send to recipient channels
                for ch in recipient_channels:
                    await self.channel_layer.send(
                        ch,
                        {
                            "type": "chat_message_delivery",
                            "sender": self.user.username,
                            "recipient": recipient_username,
                            "message": message_text,
                            "is_private": True,
                            "timestamp": db_msg.timestamp.strftime('%H:%M')
                        }
                    )
                # Send confirmation to sender
                await self.send_json({
                    "type": "chat_message_delivery",
                    "sender": self.user.username,
                    "recipient": recipient_username,
                    "message": message_text,
                    "is_private": True,
                    "timestamp": db_msg.timestamp.strftime('%H:%M')
                })
            else:
                # Group chat
                db_msg = await self.save_chat_message_db(self.meeting_id, self.user, message_text, None)
                
                # Broadcast to whole group
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "chat_message_broadcast",
                        "sender": self.user.username,
                        "message": message_text,
                        "is_private": False,
                        "timestamp": db_msg.timestamp.strftime('%H:%M')
                    }
                )
            return

        # Host Controls (Must be host to call these)
        if self.is_host:
            if msg_type == 'admit-participant':
                target_chan = content.get('target_channel')
                if target_chan and target_chan in MeetingConsumer.active_peers.get(self.meeting_id, {}):
                    # Mark approved in memory
                    MeetingConsumer.active_peers[self.meeting_id][target_chan]["approved"] = True
                    
                    # Update database
                    peer_user_id = MeetingConsumer.active_peers[self.meeting_id][target_chan]["user_id"]
                    await self.update_participant_approval_db(self.meeting, peer_user_id, True)

                    # Send approval notification directly to peer
                    await self.channel_layer.send(
                        target_chan,
                        {
                            "type": "admitted_state",
                            "username": MeetingConsumer.active_peers[self.meeting_id][target_chan]["username"]
                        }
                    )
                    
                    # Broadcast to room that new peer is admitted so WebRTC Mesh connection starts
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "peer_joined",
                            "user_id": peer_user_id,
                            "username": MeetingConsumer.active_peers[self.meeting_id][target_chan]["username"],
                            "channel_name": target_chan
                        }
                    )
                    # Refresh host peer list
                    await self.broadcast_peer_list_to_host()

            elif msg_type == 'deny-participant':
                target_chan = content.get('target_channel')
                if target_chan and target_chan in MeetingConsumer.active_peers.get(self.meeting_id, {}):
                    await self.channel_layer.send(
                        target_chan,
                        {"type": "denied_state"}
                    )
                    # Host list refresh
                    await self.broadcast_peer_list_to_host()

            elif msg_type == 'mute-participant':
                target_id = content.get('user_id')
                # Find channel name for target_id
                for ch, info in MeetingConsumer.active_peers.get(self.meeting_id, {}).items():
                    if info['user_id'] == target_id:
                        await self.channel_layer.send(
                            ch,
                            {"type": "host_action_mute_audio"}
                        )
                        break

            elif msg_type == 'disable-participant-video':
                target_id = content.get('user_id')
                for ch, info in MeetingConsumer.active_peers.get(self.meeting_id, {}).items():
                    if info['user_id'] == target_id:
                        await self.channel_layer.send(
                            ch,
                            {"type": "host_action_disable_video"}
                        )
                        break

            elif msg_type == 'remove-participant':
                target_id = content.get('user_id')
                for ch, info in MeetingConsumer.active_peers.get(self.meeting_id, {}).items():
                    if info['user_id'] == target_id:
                        await self.channel_layer.send(
                            ch,
                            {"type": "host_action_kick"}
                        )
                        break

            elif msg_type == 'mute-all':
                # Send mute signal to all participants
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "host_action_mute_all"
                    }
                )

            elif msg_type == 'lock-meeting':
                is_locked = content.get('is_locked', False)
                await self.update_meeting_lock_db(self.meeting_id, is_locked)
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "host_action_lock_meeting",
                        "is_locked": is_locked
                    }
                )

            elif msg_type == 'end-meeting':
                await self.end_meeting_db(self.meeting_id)
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "host_action_end_meeting"
                    }
                )

    # WebSocket Group Message Handlers

    async def signal_message(self, event):
        # Relay signaling payload directly to the client
        await self.send_json({
            "type": "signal",
            "sender_channel": event["sender_channel"],
            "sender_username": event["sender_username"],
            "sender_id": event["sender_id"],
            "payload": event["payload"]
        })

    async def peer_joined(self, event):
        # Notify self that a peer joined
        if event["channel_name"] != self.channel_name:
            await self.send_json({
                "type": "peer_joined",
                "user_id": event["user_id"],
                "username": event["username"],
                "channel_name": event["channel_name"]
            })

    async def peer_left(self, event):
        # Notify client to tear down peer connection for this user
        if event["channel_name"] != self.channel_name:
            await self.send_json({
                "type": "peer_left",
                "user_id": event["user_id"],
                "username": event["username"],
                "channel_name": event["channel_name"]
            })

    async def waiting_room_request(self, event):
        # Send waiting room request only to the host
        if self.is_host:
            await self.send_json({
                "type": "waiting_room_request",
                "user_id": event["user_id"],
                "username": event["username"],
                "channel_name": event["channel_name"]
            })
            # Also refresh host's full list of peers
            await self.broadcast_peer_list_to_host()

    async def admitted_state(self, event):
        # Peer has been approved, transition out of waiting screen
        self.is_approved = True
        if self.channel_name in MeetingConsumer.active_peers.get(self.meeting_id, {}):
            MeetingConsumer.active_peers[self.meeting_id][self.channel_name]["approved"] = True

        await self.send_json({
            "type": "connection_success",
            "role": "participant",
            "username": event["username"],
            "is_approved": True
        })

    async def denied_state(self, event):
        await self.send_json({
            "type": "denied"
        })
        await self.close(code=4004)

    async def chat_message_broadcast(self, event):
        await self.send_json({
            "type": "chat_message",
            "sender": event["sender"],
            "recipient": None,
            "message": event["message"],
            "is_private": False,
            "timestamp": event["timestamp"]
        })

    async def chat_message_delivery(self, event):
        await self.send_json({
            "type": "chat_message",
            "sender": event["sender"],
            "recipient": event["recipient"],
            "message": event["message"],
            "is_private": True,
            "timestamp": event["timestamp"]
        })

    async def host_action_mute_audio(self, event):
        await self.send_json({
            "type": "host_command",
            "action": "mute_audio"
        })

    async def host_action_disable_video(self, event):
        await self.send_json({
            "type": "host_command",
            "action": "disable_video"
        })

    async def host_action_kick(self, event):
        await self.send_json({
            "type": "host_command",
            "action": "kick"
        })
        await self.close(code=4005)

    async def host_action_mute_all(self, event):
        # Mute everyone except the host
        if not self.is_host:
            await self.send_json({
                "type": "host_command",
                "action": "mute_audio"
            })

    async def host_action_lock_meeting(self, event):
        await self.send_json({
            "type": "room_locked_status",
            "is_locked": event["is_locked"]
        })

    async def host_action_end_meeting(self, event):
        await self.send_json({
            "type": "host_command",
            "action": "end_meeting"
        })
        await self.close(code=4006)

    # Host-specific broadcast utility
    async def broadcast_peer_list_to_host(self):
        peers_list = []
        waiting_list = []
        
        meeting_peers = MeetingConsumer.active_peers.get(self.meeting_id, {})
        for ch, info in meeting_peers.items():
            peer_data = {
                "user_id": info["user_id"],
                "username": info["username"],
                "role": info["role"],
                "channel_name": ch
            }
            if info["approved"]:
                peers_list.append(peer_data)
            else:
                waiting_list.append(peer_data)
        
        await self.send_json({
            "type": "peer_lists_update",
            "active_participants": peers_list,
            "waiting_participants": waiting_list
        })


    # Database async wrappers

    @database_sync_to_async
    def get_meeting_db(self, meeting_id):
        try:
            return Meeting.objects.get(meeting_id=meeting_id)
        except Meeting.DoesNotExist:
            return None

    @database_sync_to_async
    def get_meeting_host_db(self, meeting):
        return meeting.host

    @database_sync_to_async
    def register_participant_db(self, meeting, user, role, is_approved):
        p, created = MeetingParticipant.objects.get_or_create(
            meeting=meeting,
            user=user,
            defaults={'role': role, 'is_approved': is_approved}
        )
        if not created:
            p.role = role
            p.is_approved = is_approved
            p.left_at = None
            p.save()
        return p

    @database_sync_to_async
    def update_participant_approval_db(self, meeting, user_id, is_approved):
        MeetingParticipant.objects.filter(meeting=meeting, user_id=user_id).update(is_approved=is_approved)

    @database_sync_to_async
    def mark_participant_left_db(self, meeting, user):
        from django.utils import timezone
        MeetingParticipant.objects.filter(meeting=meeting, user=user, left_at__isnull=True).update(left_at=timezone.now())

    @database_sync_to_async
    def save_chat_message_db(self, meeting_id, sender, message, recipient_username=None):
        recipient = None
        if recipient_username:
            try:
                recipient = User.objects.get(username=recipient_username)
            except User.DoesNotExist:
                pass
        meeting = Meeting.objects.get(meeting_id=meeting_id)
        return ChatMessage.objects.create(
            meeting=meeting,
            sender=sender,
            recipient=recipient,
            message=message
        )

    @database_sync_to_async
    def update_meeting_lock_db(self, meeting_id, is_locked):
        Meeting.objects.filter(meeting_id=meeting_id).update(is_locked=is_locked)

    @database_sync_to_async
    def end_meeting_db(self, meeting_id):
        Meeting.objects.filter(meeting_id=meeting_id).update(is_active=False)
