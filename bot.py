ximport asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import threading
import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# SERVEUR WEB (Pour hébergement Cloud)
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is online!")

    def log_message(self, format, *args):
        pass

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

threading.Thread(target=start_web_server, daemon=True).start()

# ============================================================
# CONFIGURATION GÉNÉRALE
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")
FICHIER_DONNEES = "donnees_bot.json"
NOM_CATEGORIE_SALONS = "🌿 ESPACES PERSONNELS"
CATEGORIE_TICKETS = "🎫・TICKETS"
ROLE_STAFF_ID = 0  # Remplace par l'ID de ton rôle staff si besoin

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True
intents.invites = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

cache_invitations = {}
vocal_sessions = {}

# ============================================================
# GESTION DES DONNÉES ISOLÉES PAR SERVEUR
# ============================================================

def structure_guild_defaut():
    return {
        "tournoi_invites": {
            "actif": False,
            "invitateurs": {},
        },
        "tickets_config": {
            "channel_logs_id": None,
            "tickets_actifs": {},
        },
        "logs_config": {
            "channel_logs_commandes_id": None
        },
        "tableau_bord": {"channel_id": None, "message_id": None},
        "statistiques": {
            "aujourdhui_plus": 0,
            "aujourdhui_moins": 0,
            "semaine_plus": 0,
            "semaine_moins": 0,
            "record_membres": 0,
            "historique_7j": {},
        },
        "temps_vocal": {}
    }

def charger_donnees():
    if not os.path.exists(FICHIER_DONNEES):
        return {}
    try:
        with open(FICHIER_DONNEES, "r", encoding="utf-8") as fichier:
            return json.load(fichier)
    except Exception:
        return {}

donnees = charger_donnees()

def obtenir_donnees_guild(guild_id):
    gid_str = str(guild_id)
    if gid_str not in donnees:
        donnees[gid_str] = structure_guild_defaut()
    else:
        defaut = structure_guild_defaut()
        for cle, valeur in defaut.items():
            donnees[gid_str].setdefault(cle, valeur)
    return donnees[gid_str]

def sauvegarder():
    try:
        fichier_temp = FICHIER_DONNEES + ".tmp"
        with open(fichier_temp, "w", encoding="utf-8") as fichier:
            json.dump(donnees, fichier, indent=4, ensure_ascii=False)
        os.replace(fichier_temp, FICHIER_DONNEES)
    except Exception as erreur:
        print(f"❌ Erreur sauvegarde : {erreur}")

def est_staff(interaction: discord.Interaction):
    if interaction.user.guild_permissions.administrator:
        return True
    if ROLE_STAFF_ID and any(r.id == ROLE_STAFF_ID for r in interaction.user.roles):
        return True
    return False

# ============================================================
# SYSTÈME DE LOGS DES COMMANDES SLASH
# ============================================================

@tree.command(name="setup_logs_commandes", description="[STAFF] Configurer le salon de logs de TOUTES les commandes")
@app_commands.describe(salon="Le salon textuel réservé au Staff pour les logs")
async def setup_logs_commandes(interaction: discord.Interaction, salon: discord.TextChannel):
    if not est_staff(interaction):
        await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
        return

    gdata = obtenir_donnees_guild(interaction.guild_id)
    gdata.setdefault("logs_config", {})["channel_logs_commandes_id"] = salon.id
    sauvegarder()
    await interaction.response.send_message(f"✅ Salon des logs de commandes configuré sur {salon.mention}.", ephemeral=True)

@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.Command):
    if not interaction.guild:
        return

    gdata = obtenir_donnees_guild(interaction.guild_id)
    log_id = gdata.get("logs_config", {}).get("channel_logs_commandes_id")
    if not log_id:
        return

    log_channel = interaction.guild.get_channel(log_id)
    if not log_channel:
        return

    options_texte = []
    if interaction.data and "options" in interaction.data:
        for opt in interaction.data["options"]:
            options_texte.append(f"• **{opt.get('name')}** : `{opt.get('value')}`")

    details_options = "\n".join(options_texte) if options_texte else "*Aucune option*"

    embed = discord.Embed(
        title="⚙️ Commande Exécutée",
        color=0x34495E,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="👤 Utilisateur", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=True)
    embed.add_field(name="💬 Salon", value=f"{interaction.channel.mention} (`#{interaction.channel.name}`)", inline=True)
    embed.add_field(name="💻 Commande", value=f"`/{command.name}`", inline=False)
    embed.add_field(name="📝 Paramètres", value=details_options, inline=False)
    embed.set_footer(text=f"Logs • ID : {interaction.id}")

    try:
        await log_channel.send(embed=embed)
    except Exception as e:
        print(f"❌ Erreur envoi log commande : {e}")

# ============================================================
# COMMANDE /commandes
# ============================================================

@tree.command(name="commandes", description="Afficher la liste de toutes les commandes du bot")
async def liste_commandes(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📜 Liste des Commandes",
        description="Voici l'ensemble des commandes disponibles :",
        color=0x2ECC71,
    )
    embed.add_field(
        name="🌿 Salons Personnels (`/salon`)",
        value=(
            "• `/salon créer <nom>` : Créer ton espace vocal et textuel.\n"
            "• `/salon supprimer` : Supprimer tes salons.\n"
            "• `/salon ajouter joueur @membre` : Donner l'accès à ton espace.\n"
            "• `/salon retirer joueur @membre` : Retirer l'accès à un membre."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎙️ Classements & Communauté",
        value=(
            "• `/top_voc` : Classement des membres les plus actifs en vocal.\n"
            "• `/invites` : Voir ton nombre d'invitations.\n"
            "• `/tournoi classement` : Afficher le classement du tournoi.\n"
            "• `/rumeur <texte>` : Propager une rumeur anonymement."
        ),
        inline=False,
    )
    embed.set_footer(text="Bot Jardinage & Communauté")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# COMMANDE /embed (STAFF)
# ============================================================

@tree.command(name="embed", description="[STAFF] Créer et envoyer un message Embed personnalisé")
@app_commands.describe(
    titre="Titre de l'embed",
    description="Contenu / Description de l'embed",
    couleur_hex="Couleur au format Hex (ex: 2ECC71)",
    image_url="Lien direct vers une image (Optionnel)"
)
async def creer_embed(interaction: discord.Interaction, titre: str, description: str, couleur_hex: str = "2ECC71", image_url: str = None):
    if not est_staff(interaction):
        await interaction.response.send_message("❌ Cette commande est réservée au staff.", ephemeral=True)
        return

    clean_hex = couleur_hex.lstrip("#")
    try:
        color_val = int(clean_hex, 16)
    except ValueError:
        color_val = 0x2ECC71

    embed = discord.Embed(
        title=titre,
        description=description.replace("\\n", "\n"),
        color=color_val,
        timestamp=datetime.now(timezone.utc),
    )
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text=f"Posté par {interaction.user.display_name}")

    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Embed envoyé avec succès !", ephemeral=True)

# ============================================================
# COMMANDE /rumeur
# ============================================================

@tree.command(name="rumeur", description="Propager une rumeur anonymement")
@app_commands.describe(rumeur="La rumeur à propager")
async def propager_rumeur(interaction: discord.Interaction, rumeur: str):
    embed = discord.Embed(
        title="🌿 Une rumeur se propage...",
        description=f"« *{rumeur}* »",
        color=0x7289DA,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Auteur anonyme")

    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("🤫 Ta rumeur a été propagée en toute discrétion.", ephemeral=True)

# ============================================================
# SYSTÈME DE SUIVI VOCAL & TOP VOC ISOLÉ
# ============================================================

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    guild = before.channel.guild if before.channel else after.channel.guild
    if not guild:
        return

    now = datetime.now(timezone.utc).timestamp()
    user_id_str = str(member.id)
    gdata = obtenir_donnees_guild(guild.id)

    if before.channel is None and after.channel is not None:
        vocal_sessions[(guild.id, member.id)] = now
    elif before.channel is not None and after.channel is None:
        start_time = vocal_sessions.pop((guild.id, member.id), None)
        if start_time:
            duree = int(now - start_time)
            temps_voc = gdata.setdefault("temps_vocal", {})
            temps_voc[user_id_str] = temps_voc.get(user_id_str, 0) + duree
            sauvegarder()

@tree.command(name="top_voc", description="Afficher le classement des membres les plus actifs en vocal")
async def top_voc(interaction: discord.Interaction):
    gdata = obtenir_donnees_guild(interaction.guild_id)
    temps_voc = gdata.get("temps_vocal", {})
    if not temps_voc:
        await interaction.response.send_message("📊 Aucune donnée vocale enregistrée sur ce serveur pour le moment.", ephemeral=True)
        return

    trie = sorted(temps_voc.items(), key=lambda x: x[1], reverse=True)[:10]
    description_lignes = []
    medailles = ["🥇", "🥈", "🥉"]

    for i, (user_id, secondes) in enumerate(trie):
        puce = medailles[i] if i < 3 else f"`#{i+1}`"
        heures = secondes // 3600
        minutes = (secondes % 3600) // 60
        description_lignes.append(f"{puce} <@{user_id}> — **{heures}h {minutes}min**")

    embed = discord.Embed(
        title="🎙️ Top 10 — Membres les plus actifs en Vocal",
        description="\n".join(description_lignes),
        color=0xF1C40F,
        timestamp=datetime.now(timezone.utc),
    )
    await interaction.response.send_message(embed=embed)

# ============================================================
# SYSTÈME DE TOURNOI D'INVITATIONS ISOLÉ
# ============================================================

async def mettre_a_jour_cache_invitations(guild):
    try:
        invites = await guild.invites()
        cache_invitations[guild.id] = {invite.code: (invite.uses, invite.inviter.id if invite.inviter else None) for invite in invites}
    except Exception as e:
        print(f"⚠️ Erreur cache invitations [{guild.name}] : {e}")

@tasks.loop(minutes=5)
async def verifier_invitations_en_attente():
    maintenant = datetime.now(timezone.utc).timestamp()
    delai_24h = 24 * 3600
    modifications = False

    for guild_id_str, gdata in donnees.items():
        tournoi = gdata.get("tournoi_invites", {})
        if not tournoi.get("actif"):
            continue

        invitateurs = tournoi.setdefault("invitateurs", {})
        for inviter_id, data in invitateurs.items():
            en_attente = data.get("en_attente", [])
            nouveau_en_attente = []

            for item in en_attente:
                if maintenant - item.get("rejoint_le", maintenant) >= delai_24h:
                    data["validees"] = data.get("validees", 0) + 1
                    modifications = True
                else:
                    nouveau_en_attente.append(item)

            data["en_attente"] = nouveau_en_attente

    if modifications:
        sauvegarder()

groupe_tournoi = app_commands.Group(name="tournoi", description="Gestion du tournoi d'invitations")

@groupe_tournoi.command(name="start", description="[STAFF] Lancer le tournoi d'invitations")
async def tournoi_start(interaction: discord.Interaction):
    if not est_staff(interaction):
        await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
        return

    gdata = obtenir_donnees_guild(interaction.guild_id)
    tournoi = gdata.setdefault("tournoi_invites", {"actif": False, "invitateurs": {}})
    tournoi["actif"] = True
    tournoi["invitateurs"] = {}
    sauvegarder()
    await mettre_a_jour_cache_invitations(interaction.guild)

    embed = discord.Embed(
        title="🎉 Lancement du Tournoi d'Invitations !",
        description="Le tournoi est ouvert ! Les invités doivent rester au moins **24 heures** pour valider l'invitation.",
        color=0x2ECC71
    )
    await interaction.response.send_message(embed=embed)

@groupe_tournoi.command(name="stop", description="[STAFF] Arrêter le tournoi d'invitations")
async def tournoi_stop(interaction: discord.Interaction):
    if not est_staff(interaction):
        await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
        return

    gdata = obtenir_donnees_guild(interaction.guild_id)
    tournoi = gdata.setdefault("tournoi_invites", {"actif": False, "invitateurs": {}})
    tournoi["actif"] = False
    sauvegarder()

    embed = discord.Embed(title="🛑 Fin du Tournoi", description="Le tournoi est terminé !", color=0xE74C3C)
    await interaction.response.send_message(embed=embed)

@groupe_tournoi.command(name="classement", description="Afficher le classement des invitateurs")
async def tournoi_classement(interaction: discord.Interaction):
    gdata = obtenir_donnees_guild(interaction.guild_id)
    invitateurs = gdata.get("tournoi_invites", {}).get("invitateurs", {})
    if not invitateurs:
        await interaction.response.send_message("📊 Aucune invitation enregistrée pour le moment.", ephemeral=True)
        return

    trie = sorted(invitateurs.items(), key=lambda x: (x[1].get("validees", 0), len(x[1].get("en_attente", []))), reverse=True)[:10]
    lignes = [f"`#{i+1}` <@{uid}> — **{data.get('validees', 0)}** validée(s)" for i, (uid, data) in enumerate(trie)]

    embed = discord.Embed(title="🏆 Classement du Tournoi", description="\n".join(lignes), color=0xF1C40F)
    await interaction.response.send_message(embed=embed)

tree.add_command(groupe_tournoi)

@tree.command(name="invites", description="Consulter tes statistiques d'invitations")
async def consulter_invites(interaction: discord.Interaction, joueur: discord.Member = None):
    cible = joueur or interaction.user
    gdata = obtenir_donnees_guild(interaction.guild_id)
    data = gdata.get("tournoi_invites", {}).get("invitateurs", {}).get(str(cible.id), {"validees": 0, "en_attente": []})

    embed = discord.Embed(title=f"📩 Invitations de {cible.display_name}", color=0x3498DB)
    embed.add_field(name="✅ Validées (24h+)", value=f"**{data.get('validees', 0)}**", inline=True)
    embed.add_field(name="⏳ En attente", value=f"**{len(data.get('en_attente', []))}**", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_member_join(member):
    if member.bot:
        return
    
    gdata = obtenir_donnees_guild(member.guild.id)
    stats = gdata.setdefault("statistiques", {})
    stats["aujourdhui_plus"] = stats.get("aujourdhui_plus", 0) + 1
    stats["semaine_plus"] = stats.get("semaine_plus", 0) + 1
    sauvegarder()

    tournoi = gdata.get("tournoi_invites", {})
    if tournoi.get("actif"):
        guild = member.guild
        invites_avant = cache_invitations.get(guild.id, {})
        try:
            invites_apres = await guild.invites()
            inviter_id = None
            for invite in invites_apres:
                uses_avant, owner_id = invites_avant.get(invite.code, (0, None))
                if invite.uses > uses_avant:
                    inviter_id = invite.inviter.id if invite.inviter else owner_id
                    break
            await mettre_a_jour_cache_invitations(guild)
            if inviter_id and inviter_id != member.id:
                data_inv = tournoi.setdefault("invitateurs", {}).setdefault(str(inviter_id), {"validees": 0, "en_attente": []})
                data_inv["en_attente"].append({"member_id": member.id, "rejoint_le": datetime.now(timezone.utc).timestamp()})
                sauvegarder()
        except Exception as e:
            print(f"Erreur invite join : {e}")

@bot.event
async def on_member_remove(member):
    if member.bot:
        return
    
    gdata = obtenir_donnees_guild(member.guild.id)
    stats = gdata.setdefault("statistiques", {})
    stats["aujourdhui_moins"] = stats.get("aujourdhui_moins", 0) + 1
    stats["semaine_moins"] = stats.get("semaine_moins", 0) + 1
    sauvegarder()

    tournoi = gdata.get("tournoi_invites", {})
    if tournoi.get("actif"):
        modifs = False
        for inviter_id, data in tournoi.get("invitateurs", {}).items():
             nouveau = [item for item in data.get("en_attente", []) if item.get("member_id") != member.id]
             if len(nouveau) != len(data.get("en_attente", [])):
                 data["en_attente"] = nouveau
                 modifs = True
        if modifs:
            sauvegarder()

# ============================================================
# TABLEAU DE BORD DYNAMIQUE ISOLÉ
# ============================================================

@tasks.loop(minutes=1)
async def update_tableau_de_board():
    for guild in bot.guilds:
        gdata = obtenir_donnees_guild(guild.id)
        board_config = gdata.get("tableau_bord", {})
        channel_id = board_config.get("channel_id")
        message_id = board_config.get("message_id")

        if not channel_id or not message_id:
            continue

        channel = guild.get_channel(channel_id)
        if not channel:
            continue

        try:
            message = await channel.fetch_message(message_id)
        except Exception:
            continue

        total_membres = guild.member_count
        stats = gdata.setdefault("statistiques", {})

        aujourdhui_plus = stats.get("aujourdhui_plus", 0)
        aujourdhui_moins = stats.get("aujourdhui_moins", 0)
        semaine_plus = stats.get("semaine_plus", 0)
        semaine_moins = stats.get("semaine_moins", 0)

        record_membres = stats.get("record_membres", total_membres)
        if total_membres > record_membres:
            record_membres = total_membres
            stats["record_membres"] = record_membres
            sauvegarder()

        historique = stats.setdefault("historique_7j", {})
        date_aujourdhui = datetime.now(timezone.utc).strftime("%m-%d")
        historique[date_aujourdhui] = total_membres

        cles_triees = sorted(historique.keys())[-7:]
        lignes_evolution = [f"{date} : {historique[date]}" for date in cles_triees]
        texte_evolution = "\n".join(lignes_evolution) if lignes_evolution else f"{date_aujourdhui} : {total_membres}"

        contenu_message = (
            "📈 **TABLEAU DE BORD DU SERVEUR**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "👥 **STATISTIQUES DISCORD**\n"
            f"• Membres actuels : {total_membres}\n"
            f"• Aujourd'hui : +{aujourdhui_plus} / -{aujourdhui_moins}\n"
            f"• Cette semaine : +{semaine_plus} / -{semaine_moins}\n"
            f"• Record de membres : {record_membres}\n\n"
            "📊 **ÉVOLUTION DES MEMBRES — 7 JOURS**\n"
            f"{texte_evolution}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "*Mise à jour automatique toutes les minutes*"
        )

        try:
            await message.edit(content=contenu_message)
        except Exception as e:
            print(f"❌ Erreur mise à jour tableau de bord [{guild.name}] : {e}")

@tree.command(name="setup_tableau_de_bord", description="[STAFF] Initialiser le message du tableau de bord")
async def setup_tableau_de_bord(interaction: discord.Interaction):
    if not est_staff(interaction):
        await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
        return

    msg = await interaction.channel.send("⌛ *Initialisation du tableau de bord...*")
    gdata = obtenir_donnees_guild(interaction.guild_id)
    gdata["tableau_bord"] = {
        "channel_id": interaction.channel.id,
        "message_id": msg.id,
    }
    sauvegarder()

    if not update_tableau_de_board.is_running():
        update_tableau_de_board.start()

    await interaction.response.send_message("✅ Tableau de bord créé et configuré dans ce salon !", ephemeral=True)

# ============================================================
# SYSTÈME DE TICKETS ISOLÉ
# ============================================================

class MenuChoixTicket(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Support / Aide", description="Une question ou un souci sur le serveur", emoji="🌱", value="aide"),
            discord.SelectOption(label="Partenariat", description="Proposer un partenariat", emoji="🤝", value="partenariat"),
            discord.SelectOption(label="Signalement", description="Signaler un membre ou un comportement", emoji="⚠️", value="signalement"),
            discord.SelectOption(label="Autre", description="Autre demande spécifique", emoji="📩", value="autre"),
        ]
        super().__init__(
            placeholder="Sélectionne la nature de ton ticket...",
            min_values=1, max_values=1, options=options, custom_id="select_nature_ticket"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        type_ticket = self.values[0]
        membre = interaction.user
        gdata = obtenir_donnees_guild(interaction.guild_id)

        log_id = gdata.get("tickets_config", {}).get("channel_logs_id")
        if not log_id:
            await interaction.followup.send("❌ Le salon de logs des tickets n'a pas été configuré (`/setup_logs_tickets`).", ephemeral=True)
            return

        log_channel = interaction.guild.get_channel(log_id)
        if not log_channel:
            await interaction.followup.send("❌ Le salon des logs est introuvable.", ephemeral=True)
            return

        role_staff = interaction.guild.get_role(ROLE_STAFF_ID) if ROLE_STAFF_ID else None
        ping_content = role_staff.mention if role_staff else "@here"

        embed_log = discord.Embed(
            title="📥 Nouvelle demande de Ticket",
            description=f"Le membre {membre.mention} demande l'ouverture d'un ticket.",
            color=0xF1C40F,
            timestamp=datetime.now(timezone.utc),
        )
        embed_log.add_field(name="👤 Demandeur", value=f"{membre.mention} (`{membre.id}`)", inline=True)
        embed_log.add_field(name="🏷️ Type de Ticket", value=f"`{type_ticket.upper()}`", inline=True)
        embed_log.set_footer(text="En attente de validation par l'équipe...")

        view_valider = ValiderTicketView(membre_id=membre.id, type_ticket=type_ticket)
        await log_channel.send(content=ping_content, embed=embed_log, view=view_valider)
        await interaction.followup.send("⏳ Ta demande de ticket a été envoyée au staff !", ephemeral=True)

class ValiderTicketView(discord.ui.View):
    def __init__(self, membre_id: int = 0, type_ticket: str = ""):
        super().__init__(timeout=None)
        self.membre_id = membre_id
        self.type_ticket = type_ticket

    @discord.ui.button(label="Accepter", style=discord.ButtonStyle.success, emoji="✅", custom_id="bouton_accepter_ticket")
    async def accepter(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not est_staff(interaction):
            await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        gdata = obtenir_donnees_guild(guild.id)
        embed_log = interaction.message.embeds[0]

        membre = guild.get_member(self.membre_id)
        if not membre:
            await interaction.followup.send("❌ Le joueur est introuvable.", ephemeral=True)
            return

        categorie = discord.utils.get(guild.categories, name=CATEGORIE_TICKETS)
        if not categorie:
            categorie = await guild.create_category(CATEGORIE_TICKETS)

        nom_salon = f"{self.type_ticket}-{membre.name.lower().replace(' ', '-')}"
        permissions = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            membre: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }

        role_staff = guild.get_role(ROLE_STAFF_ID) if ROLE_STAFF_ID else None
        if role_staff:
            permissions[role_staff] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)

        salon_ticket = await guild.create_text_channel(name=nom_salon, category=categorie, overwrites=permissions)

        gdata.setdefault("tickets_config", {}).setdefault("tickets_actifs", {})[str(salon_ticket.id)] = {
            "owner_id": membre.id,
            "type": self.type_ticket,
            "ouvert_a": datetime.now(timezone.utc).isoformat(),
        }
        sauvegarder()

        for child in self.children:
            child.disabled = True

        embed_log.color = 0x2ECC71
        embed_log.set_footer(text=f"✅ Accepté par {interaction.user.display_name} -> #{nom_salon}")
        await interaction.message.edit(embed=embed_log, view=self)

        embed_welcome = discord.Embed(
            title=f"🎫 Ticket {self.type_ticket.upper()} — {membre.display_name}",
            description=f"Bonjour {membre.mention} !\nTon ticket a été accepté. Explique ton problème ici.",
            color=0x3498DB,
        )
        await salon_ticket.send(embed=embed_welcome, view=FermerTicketView())
        await interaction.followup.send(f"✅ Ticket accepté ! Salon créé : {salon_ticket.mention}", ephemeral=True)

    @discord.ui.button(label="Refuser", style=discord.ButtonStyle.danger, emoji="❌", custom_id="bouton_refuser_ticket")
    async def refuser(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not est_staff(interaction):
            await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        for child in self.children:
            child.disabled = True

        embed_log = interaction.message.embeds[0]
        embed_log.color = 0xE74C3C
        embed_log.set_footer(text=f"❌ Refusé par {interaction.user.display_name}")
        await interaction.message.edit(embed=embed_log, view=self)
        await interaction.followup.send("❌ Ticket refusé.", ephemeral=True)

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MenuChoixTicket())

class FermerTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fermer le ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="bouton_fermer_ticket")
    async def fermer_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Fermeture du ticket dans 5 secondes...")
        channel_id_str = str(interaction.channel.id)
        gdata = obtenir_donnees_guild(interaction.guild_id)
        tickets_actifs = gdata.get("tickets_config", {}).get("tickets_actifs", {})
        
        if channel_id_str in tickets_actifs:
            del tickets_actifs[channel_id_str]
            sauvegarder()

        await asyncio.sleep(5)
        await interaction.channel.delete()

@tree.command(name="setup_logs_tickets", description="[STAFF] Définir le salon de réception des demandes de tickets")
@app_commands.describe(salon="Le salon textuel de logs des tickets")
async def setup_logs_tickets(interaction: discord.Interaction, salon: discord.TextChannel):
    if not est_staff(interaction):
        await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
        return

    gdata = obtenir_donnees_guild(interaction.guild_id)
    gdata.setdefault("tickets_config", {})["channel_logs_id"] = salon.id
    sauvegarder()
    await interaction.response.send_message(f"✅ Salon des logs de tickets configuré sur {salon.mention}.", ephemeral=True)

@tree.command(name="setup_panneau_tickets", description="[STAFF] Envoyer le message de création des tickets")
async def setup_panneau_tickets(interaction: discord.Interaction):
    if not est_staff(interaction):
        await interaction.response.send_message("❌ Permission insuffisante.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎫 Support & Demandes de Ticket",
        description="Besoin d'aide ou de contacter l'équipe ? Sélectionne la raison de ton ticket dans le menu ci-dessous.",
        color=0x3498DB,
    )
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("✅ Panneau de tickets envoyé dans ce salon !", ephemeral=True)

# ============================================================
# SYSTÈME DE SALONS PERSONNELS (/salon)
# ============================================================

async def obtenir_ou_creer_categorie(guild: discord.Guild):
    cat = discord.utils.find(lambda c: NOM_CATEGORIE_SALONS.lower() in c.name.lower(), guild.categories)
    if cat:
        return cat
    return await guild.create_category(NOM_CATEGORIE_SALONS)

groupe_salon = app_commands.Group(name="salon", description="Gestion de ton espace personnel")

@groupe_salon.command(name="créer", description="Créer ton espace personnel (vocal et textuel)")
@app_commands.describe(nom="Nom de ton espace")
async def salon_creer(interaction: discord.Interaction, nom: str):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    membre = interaction.user

    try:
        cat = await obtenir_ou_creer_categorie(guild)
    except discord.Forbidden:
        await interaction.followup.send("❌ Le bot n'a pas les permissions nécessaires.", ephemeral=True)
        return

    nom_clean = nom.lower().replace(" ", "-")
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False, connect=False),
        membre: discord.PermissionOverwrite(read_messages=True, send_messages=True, connect=True, speak=True, manage_channels=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, connect=True, manage_channels=True),
    }

    salon_texte = await guild.create_text_channel(name=f"💬-{nom_clean}", category=cat, overwrites=overwrites)
    salon_vocal = await guild.create_voice_channel(name=f"🔊 {nom}", category=cat, overwrites=overwrites)

    embed = discord.Embed(
        title=f"🌿 Espace de {membre.display_name}",
        description="Bienvenue dans ton espace privé !\n• `/salon ajouter joueur @membre`\n• `/salon supprimer`",
        color=0x2ECC71
    )
    await salon_texte.send(embed=embed)
    await interaction.followup.send(f"✅ Espace créé !\n• Texte : {salon_texte.mention}\n• Vocal : {salon_vocal.mention}", ephemeral=True)

@groupe_salon.command(name="supprimer", description="Supprimer ton espace personnel")
async def salon_supprimer(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    cat = await obtenir_ou_creer_categorie(interaction.guild)
    salons = [c for c in cat.channels if c.permissions_for(interaction.user).manage_channels]

    if not salons:
        await interaction.followup.send("❌ Tu ne possèdes aucun espace à supprimer.", ephemeral=True)
        return

    for c in salons:
        await c.delete()
    await interaction.followup.send("🗑️ Ton espace a été supprimé.", ephemeral=True)

groupe_ajouter = app_commands.Group(name="ajouter", description="Ajouter un accès")
@groupe_ajouter.command(name="joueur", description="Donner l'accès à ton espace à un membre")
@app_commands.describe(joueur="Le membre à inviter")
async def ajouter_joueur(interaction: discord.Interaction, joueur: discord.Member):
    await interaction.response.defer(ephemeral=True)
    cat = await obtenir_ou_creer_categorie(interaction.guild)
    salons = [c for c in cat.channels if c.permissions_for(interaction.user).manage_channels]

    if not salons:
        await interaction.followup.send("❌ Tu ne possèdes aucun espace.", ephemeral=True)
        return

    for c in salons:
        if isinstance(c, discord.TextChannel):
            await c.set_permissions(joueur, read_messages=True, send_messages=True)
        elif isinstance(c, discord.VoiceChannel):
            await c.set_permissions(joueur, read_messages=True, connect=True, speak=True)

    await interaction.followup.send(f"✅ {joueur.mention} a désormais accès à ton espace.", ephemeral=True)

groupe_salon.add_command(groupe_ajouter)

groupe_retirer = app_commands.Group(name="retirer", description="Retirer un accès")
@groupe_retirer.command(name="joueur", description="Retirer l'accès à ton espace à un membre")
@app_commands.describe(joueur="Le membre à retirer")
async def retirer_joueur(interaction: discord.Interaction, joueur: discord.Member):
    await interaction.response.defer(ephemeral=True)
    cat = await obtenir_ou_creer_categorie(interaction.guild)
    salons = [c for c in cat.channels if c.permissions_for(interaction.user).manage_channels]

    if not salons:
        await interaction.followup.send("❌ Tu ne possèdes aucun espace.", ephemeral=True)
        return

    for c in salons:
        await c.set_permissions(joueur, overwrite=None)
        if isinstance(c, discord.VoiceChannel) and joueur.voice and joueur.voice.channel == c:
            await joueur.move_to(None)

    await interaction.followup.send(f"🚫 {joueur.mention} n'a plus accès à ton espace.", ephemeral=True)

groupe_salon.add_command(groupe_retirer)
tree.add_command(groupe_salon)

# ============================================================
# ÉVÉNEMENT ON_READY
# ============================================================

@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user}")
    
    bot.add_view(TicketView())
    bot.add_view(FermerTicketView())

    if not verifier_invitations_en_attente.is_running():
        verifier_invitations_en_attente.start()
    if not update_tableau_de_board.is_running():
        update_tableau_de_board.start()

    for guild in bot.guilds:
        await mettre_a_jour_cache_invitations(guild)

    try:
        synced = await tree.sync()
        print(f"⚡ {len(synced)} commande(s) synchronisée(s).")
    except Exception as e:
        print(f"❌ Erreur de synchronisation : {e}")

bot.run(TOKEN)
