#include "WallClimbComponent.h"

#include "GameFramework/Character.h"
#include "Engine/World.h"

UWallClimbComponent::UWallClimbComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
    PrimaryComponentTick.TickInterval = 0.05f;
}

void UWallClimbComponent::TickComponent(
    float DeltaTime,
    ELevelTick TickType,
    FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

    if (!bIsClimbing)
    {
        return;
    }

    // 攀爬过程中定期复核墙面是否还在
    ClimbTraceTimer += DeltaTime;
    if (ClimbTraceTimer >= 0.1f)
    {
        ClimbTraceTimer = 0.0f;

        if (!IsOnClimbableSurface())
        {
            StopClimb();
        }
    }
}

bool UWallClimbComponent::TryStartClimb()
{
    ACharacter* Owner = Cast<ACharacter>(GetOwner());
    if (!Owner || !GetWorld())
    {
        return false;
    }

    FHitResult Hit;
    const FVector Start = Owner->GetActorLocation();
    const FVector End = Start + Owner->GetActorForwardVector() * TraceDistance;

    FCollisionQueryParams Params;
    Params.AddIgnoredActor(Owner);
    Params.bTraceComplex = false;

    const bool bHit = GetWorld()->LineTraceSingleByChannel(
        Hit, Start, End, ECC_Visibility, Params);

    if (!bHit)
    {
        return false;
    }

    // 只有接近垂直的墙面才可攀爬
    const float SurfaceAngle = FMath::RadiansToDegrees(
        FMath::Acos(FMath::Clamp(Hit.ImpactNormal.Z, -1.0f, 1.0f)));

    const float DeviationFromVertical = FMath::Abs(SurfaceAngle - 90.0f);
    if (DeviationFromVertical > MaxClimbableAngle)
    {
        UE_LOG(LogTemp, Warning, TEXT("Surface too slanted to climb: angle=%.1f"), SurfaceAngle);
        return false;
    }

    SurfaceNormal = Hit.ImpactNormal;
    bIsClimbing = true;
    ClimbStartZ = Owner->GetActorLocation().Z;

    return true;
}

void UWallClimbComponent::StopClimb()
{
    bIsClimbing = false;
    SurfaceNormal = FVector::ZeroVector;
    ClimbStartZ = 0.0f;
    ClimbTraceTimer = 0.0f;
}

bool UWallClimbComponent::IsOnClimbableSurface()
{
    ACharacter* Owner = Cast<ACharacter>(GetOwner());
    if (!Owner || !GetWorld())
    {
        return false;
    }

    // 超过攀爬高度上限后不允许继续
    if (bIsClimbing && (Owner->GetActorLocation().Z - ClimbStartZ) > MaxClimbHeight)
    {
        return false;
    }

    FHitResult Hit;
    const FVector Start = Owner->GetActorLocation();
    const FVector End = Start + Owner->GetActorForwardVector() * TraceDistance;

    FCollisionQueryParams Params;
    Params.AddIgnoredActor(Owner);

    const bool bHit = GetWorld()->LineTraceSingleByChannel(
        Hit, Start, End, ECC_Visibility, Params);

    if (!bHit)
    {
        return false;
    }

    const float SurfaceAngle = FMath::RadiansToDegrees(
        FMath::Acos(FMath::Clamp(Hit.ImpactNormal.Z, -1.0f, 1.0f)));

    return FMath::Abs(SurfaceAngle - 90.0f) <= MaxClimbableAngle;
}
