package org.lifeguard.wear

import androidx.concurrent.futures.CallbackToFutureAdapter
import androidx.wear.protolayout.ActionBuilders
import androidx.wear.protolayout.ColorBuilders.argb
import androidx.wear.protolayout.DimensionBuilders
import androidx.wear.protolayout.DimensionBuilders.expand
import androidx.wear.protolayout.DimensionBuilders.sp
import androidx.wear.protolayout.LayoutElementBuilders
import androidx.wear.protolayout.ModifiersBuilders
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.protolayout.TimelineBuilders
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders
import androidx.wear.tiles.TileService
import com.google.common.util.concurrent.ListenableFuture

/**
 * Tile: עיגול "SOS" שפותח את מסך ה-SOS בשעון. בכוונה **לא** שולח בהקשה: ההפעלה תמיד בהחזקה של 2 שנ' + חלון ביטול
 * (הקשה אחת על Tile קלה מדי להפעלה בטעות).
 */
class SosTileService : TileService() {

    override fun onTileRequest(requestParams: RequestBuilders.TileRequest): ListenableFuture<TileBuilders.Tile> =
        CallbackToFutureAdapter.getFuture { c ->
            c.set(TileBuilders.Tile.Builder()
                .setResourcesVersion(RES_VERSION)
                .setTileTimeline(TimelineBuilders.Timeline.fromLayoutElement(layout()))
                .build())
            "lifeguard-sos-tile"
        }

    override fun onTileResourcesRequest(requestParams: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> =
        CallbackToFutureAdapter.getFuture { c ->
            c.set(ResourceBuilders.Resources.Builder().setVersion(RES_VERSION).build())
            "lifeguard-sos-tile-res"
        }

    private fun layout(): LayoutElementBuilders.LayoutElement {
        val open = ModifiersBuilders.Clickable.Builder()
            .setId("open_sos")
            .setOnClick(ActionBuilders.LaunchAction.Builder()
                .setAndroidActivity(ActionBuilders.AndroidActivity.Builder()
                    .setPackageName(packageName)
                    .setClassName(MainActivity::class.java.name)
                    .build())
                .build())
            .build()
        val circle = LayoutElementBuilders.Box.Builder()
            .setWidth(DimensionBuilders.dp(CIRCLE_DP))
            .setHeight(DimensionBuilders.dp(CIRCLE_DP))
            .setModifiers(ModifiersBuilders.Modifiers.Builder()
                .setClickable(open)
                .setSemantics(ModifiersBuilders.Semantics.Builder().setContentDescription(getString(R.string.sos_desc)).build())
                .setBackground(ModifiersBuilders.Background.Builder()
                    .setColor(argb(0xFFD70015.toInt()))   // lg_fill_sos
                    .setCorner(ModifiersBuilders.Corner.Builder().setRadius(DimensionBuilders.dp(CIRCLE_DP / 2)).build())
                    .build())
                .build())
            .addContent(text("SOS", 30f, bold = true))
            .build()
        return LayoutElementBuilders.Box.Builder()
            .setWidth(expand())
            .setHeight(expand())
            .addContent(LayoutElementBuilders.Column.Builder()
                .addContent(circle)
                .addContent(LayoutElementBuilders.Spacer.Builder().setHeight(DimensionBuilders.dp(6f)).build())
                .addContent(text("הקש/י לפתיחה, החזק/י במסך", 12f, bold = false))
                .build())
            .build()
    }

    private fun text(s: String, size: Float, bold: Boolean) = LayoutElementBuilders.Text.Builder()
        .setText(s)
        .setMaxLines(2)
        .setFontStyle(LayoutElementBuilders.FontStyle.Builder()
            .setSize(sp(size))
            .setColor(argb(0xFFFFFFFF.toInt()))
            .setWeight(if (bold) LayoutElementBuilders.FONT_WEIGHT_BOLD else LayoutElementBuilders.FONT_WEIGHT_NORMAL)
            .build())
        .build()

    companion object {
        private const val RES_VERSION = "1"
        private const val CIRCLE_DP = 110f
    }
}
